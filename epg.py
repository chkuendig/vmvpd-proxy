
from gevent import monkey
monkey.patch_all()
from itertools import chain
from functools import partial
from flask import Flask, Response, request, jsonify, abort, render_template

from streamlink.stream.hls import HLSStream

from streamlink.plugin.api import useragents
from streamlink_cli.utils import JSONEncoder
from streamlink import NoPluginError, PluginError, StreamError, Streamlink, __version__ as streamlink_version
from gevent.pywsgi import WSGIServer
import logging
import requests
import re
import socket
import subprocess
import os
import json
import time
import threading
streamlink_session = Streamlink()
from datetime import timezone, datetime, timedelta
from urllib.parse import urlparse, parse_qs

from lxml import etree
from lxml.cssselect import CSSSelector
hosts = {}
app = Flask(__name__)

html_parser = etree.HTMLParser()
cookies = dict(BoxId="***REMOVED***",
               YuppflixToken="***REMOVED***")


streamlink_session.set_plugin_option("yupptv","boxid",cookies["BoxId"])
streamlink_session.set_plugin_option("yupptv","yuppflixtoken",cookies["YuppflixToken"])
streamlink_session.set_option("hls-live-edge",1)

playlist_file = "yupptv.m3u"
xmltv_file = "epg.xml"

logger = logging.getLogger()
first_run=False
XMLTV_DOCTYPE = '<!DOCTYPE tv SYSTEM "https://github.com/XMLTV/xmltv/raw/master/xmltv.dtd">'
channel_urls = {}

## restore the streams from json
YuppTV = streamlink_session.get_plugins()["yupptv"]
yupptv_plugin = YuppTV("")

yupptv_plugin.session.http.headers.update({"User-Agent": useragents.CHROME})
yupptv_plugin.session.http.headers.update({"Origin": "https://www.yupptv.com"})
all_streams = {}
streams_file = "streams.json"
if os.path.isfile(streams_file):
    json_file = open(streams_file)
    all_streams = json.load(json_file)

@staticmethod
def _override_encoding(res, **kwargs):
    print("_override_encoding")
    res.encoding = "utf-8"

# https://stackoverflow.com/questions/3663450/remove-substring-only-at-the-end-of-string


def rchop(s, suffix):
    if suffix and s.endswith(suffix):
        return s[:-len(suffix)]
    return s


MAX_FILE_AGE = 48*60*60


def _file_age_in_seconds(pathname):
    return time.time() - os.path.getmtime(pathname)


def _downloadFile(filename, url):
    global downloadCount
    if (not os.path.isfile(filename) or _file_age_in_seconds(filename) > MAX_FILE_AGE):

        r = requests.get(url,  cookies=cookies)
        open(filename, 'wb').write(r.content)


def _get_stream(channel_name):
    global all_streams
    channel_streams = None
    if(channel_name not in all_streams or all_streams[channel_name]["expiry"] < datetime.now().timestamp()+600):
        # todo: take this from the epg/channels crawler
        page_url =          "https://www.yupptv.com/channels/"+channel_name+"/live"    
        #stream_url = subprocess.check_output("streamlink --stream-url --yupptv-boxid=***REMOVED*** --yupptv-yuppflixtoken=***REMOVED*** \""+page_url+"\" ", shell=True).decode("utf-8").strip()
        #if(stream_url == "None"):
        #    stream_url = subprocess.check_output("streamlink --stream-url --yupptv-boxid=***REMOVED*** --yupptv-yuppflixtoken=***REMOVED*** \""+page_url+"\" best", shell=True).decode("utf-8").strip()
        channel_streams = streamlink_session.streams(page_url )  
        stream_url = channel_streams['best'].to_manifest_url()
        if(stream_url == None):
            stream_url = channel_streams['best'].url

        # get expiry
        parsed_uri = urlparse(stream_url)
        params = parse_qs(parsed_uri.query)
        expiry = -1
        if("hdnts" in params):
            hdnts = dict(s.split('=') for s in params["hdnts"][0].split("~"))
            expiry = int(hdnts["exp"])
        elif("hdntl" in params):
            hdntl = dict(s.split('=') for s in params["hdntl"][0].split("~"))
            expiry = int(hdntl["exp"])
        elif ("e" in params):
            expiry = int(params["e"][0])
        else:
            expiry = datetime.now().timestamp()+3600*24

        # update streams
        all_streams[channel_name] = {
            "url": stream_url,
            "streams":channel_streams,
            "expiry": expiry
        }

        # persist streams in case the server get's restarted
        print("%s - updated master playlist"%channel_name)
        with open(streams_file, 'w') as outfile:
            json.dump(all_streams, outfile, cls=JSONEncoder,sort_keys=True, indent=4)
    else:
        channel_streams = HLSStream.parse_variant_playlist(yupptv_plugin.session,
                                                        all_streams[channel_name]["url"],
                                                        hooks={"response": _override_encoding})
       
        print("%s - parsed variant playlist"%channel_name)
 
    return channel_streams

def get_channel_url(channel_name):
    return channel_urls[channel_name];

def get_language(channel_name):

    epg_file = "epg.json"
    epg = {}
    if os.path.isfile(epg_file):
        json_file = open(epg_file)
        epg = json.load(json_file)
    return epg[channel_name]['language']



def get_programme(channel_name,chanid):
    epg_file = "epg.json"
    epg = {}
    refreshed = False
    if os.path.isfile(epg_file):
        json_file = open(epg_file)
        epg = json.load(json_file)
    
    if(channel_name not in epg or 
        (len(epg[channel_name]["Programs"]) > 0 and datetime.fromtimestamp(int(epg[channel_name]["Programs"][0]["endTime"])/1000) < datetime.now())):
        refreshed = True
        url = 'https://epg.api.yuppcdn.net' + '/epg/now?tenantId=3&channelIds=' + chanid
        r = requests.get(url,  cookies=cookies)
        epg_data = r.json()[0]
    
        endTime = datetime.fromtimestamp(0)
        if (len(epg_data['Programs']) > 0):
            programme = epg_data['Programs'][0]
            endTime = datetime.fromtimestamp(int(programme['endTime'])/1000)

        epg[channel_name] = epg_data
        with open(epg_file, 'w') as outfile:
            json.dump(epg, outfile, sort_keys=True, indent=4)
    else:
        epg_data= epg[channel_name]

    dummyTitle = "Program@"
    if(len(epg_data['Programs']) > 0 and epg_data['Programs'][0]['name'][0:len(dummyTitle)] != dummyTitle):
        programme = epg_data['Programs'][0]
        print("Refreshing EPG for %s"%channel_name)
        # add programme tag
        programmeElem = etree.Element("programme")

        DATE_FORMAT = '%Y%m%d%H%M%S%z'
        programmeElem.set("start", datetime.fromtimestamp(
            int(programme['startTime'])/1000).astimezone(timezone.utc).strftime(DATE_FORMAT))
        programmeElem.set("stop", datetime.fromtimestamp(
            int(programme['endTime'])/1000).astimezone(timezone.utc).strftime(DATE_FORMAT))

        programmeElem.set("channel", channel_name)
        lang = etree.SubElement(programmeElem, "language")
        lang.text = programme['language']

        title_elem = etree.SubElement(programmeElem, "title")
        title_elem.set("lang",  "en")
        title_elem.text = programme['name']

        desc_elem = etree.SubElement(programmeElem, "desc")
        desc_elem.set("lang", "en")
        desc_elem.text = programme['description']

        category_elem = etree.SubElement(programmeElem, "category")
        category_elem.set("lang", "en")
        category_elem.text = programme['genre']

        icon_elem = etree.SubElement(programmeElem, "icon")
        icon_elem.set("src", programme['thumbnailUrl'])
        if(refreshed or first_run):
            return programmeElem

    return None


def _data_refresh_loop():
    global first_run
    first_run= True
    while True:

        playlist_str = "#EXTM3U\n"
        channelNo = 0
        channels = []
        xmlTvRoot = etree.Element("tv")

        url = 'https://www.yupptv.com/livetv'
        filename = "index.html"
        _downloadFile("tmp/"+filename, url)
        raw = open("tmp/"+filename, "r").read()
        links = re.findall(
            '<a href=\'https://www.yupptv.com/channels/(.*)/live\' onclick="sendData\(localStorage.getItem\(\'page\'\),\'(.*)\' ,localStorage', raw)
        sections = ['ent-1', 'movies', 'music-1',
                    'music-unlimited---live', 'business-1', 'news']

        for link in links:
            section = link[1]

            if (section != "trending" and section != "recently-watched-live" and section not in sections):
                sections.append(section)

        for section in sections:
            if (section != "trending" and section != "recently-watched-live"):
                page = 0
                count = 0
                last_index = 0
                section_name = ""
                while(count > 0 or page == 0):

                    raw = ""

                    if(page == 0):
                        url = "https://www.yupptv.com/livetv/sections/"+section
                        filename = "index"+section+".html"
                        _downloadFile("tmp/"+filename, url)

                    else:
                        url = "https://www.yupptv.com/livetv/sectionGetMore/" + \
                            section+"/"+str(last_index)
                        filename = "index"+section+"-"+str(last_index)+".html"
                        _downloadFile("tmp/"+filename, url)

                    raw = open("tmp/"+filename, "r").read()

                    root = etree.fromstring(raw, html_parser)
                    paging_selector = CSSSelector('div.last-index')
                    count = int(paging_selector(root)[0].get('data-count'))
                    last_index = int(paging_selector(
                        root)[0].get('data-last-index'))

                    print("------------------")
                    print(section + ": "+str(last_index), count, page)
                    if(page == 0):
                        section_selector = CSSSelector('h1.section-heading')
                        section_name = section_selector(root)[0].text
                        link_selector = CSSSelector(
                            'div.livetv-cards a[href$="/live"]')
                    else:
                        link_selector = CSSSelector('a[href$="/live"]')
                    img_selector = CSSSelector('img.vert-horz-center')
                    premium_selector = CSSSelector('div.premiumicon')

                    page += 1
                    # loop over channels
                    for link in link_selector(root):
                        available = len(premium_selector(link)) == 0

                        img = img_selector(link)[0]
                        url = link.get('href')
                        logo = img.get('data-src')
                        display_name = rchop(img.get('alt'), ' Online')
                        if(available and display_name not in channels):
                            channels.append(display_name)
                            channelNo += 1
                            canonical_name = url[len(
                                "https://www.yupptv.com/channels/"):-len("/live")]
                            print(channelNo, canonical_name)

                            channelElem = etree.Element("channel")
                            channelElem.set('id', canonical_name)
                            name_elem = etree.SubElement(
                                channelElem, "display-name")
                            name_elem.text = display_name
                            ordernum_elem = etree.SubElement(
                                channelElem, "display-name")
                            ordernum_elem.text = str(channelNo)
                            icon_elem = etree.SubElement(channelElem, "icon")
                            icon_elem.set('src', logo)

                            filename = canonical_name+".html"
                            _downloadFile("tmp/"+filename, url)
                            raw = open("tmp/"+filename, "r").read()
                            chanid = re.search('chanid = \'(\d+)\'', raw).group(1)

                            language = get_language(canonical_name)
                            playlist_line1 = "#EXTINF:-1 tvh-epg=\"disable\"  tvh-chnum=\"" + \
                                str(channelNo)+"\" tvg-logo=\""+logo+"\" tvg-name=\""+canonical_name + \
                                "\" group-title=\""+section_name+";"+language+"\", "+display_name
                            # todo: run with --stream-url to pre-fetch the stream url (allows for faster switching of channels)
                            # refresh stream-url once an hour
                            # add streamlink --hls-live-edge 1 --quiet "https://ottrintl3.republicworld.com/httppush/ottrintl_republic_english/ottrintl3/master.m3u8?e=1651398600&h=aba7d3b6fdae7e76a5ec87c2f605df2f" best
                        # fixme: the following command requires an origin header and a patched hls.py to accomodate the wrong encoding
                        # maybe we can look at all the hostnames like "yuppirecloriginweb" and add them to yupptv.py
                        #  streamlink --hls-live-edge 1 --quiet --http-header Origin='https://www.yupptv.com'   "https://yuppirecloriginweb.akamaized.net/250920/colorsukhd/playlist.m3u8?hdnts=st=1640184036~exp=1640187636~acl=*~data=yuppTVCom_5_5893399_***REMOVED***_CH_81.17.17.187~hmac=a35cdb79b8c4ccaa5c9e50be868bb78274ff990f49b86742d806cb57fa4472c9" best
                            #stream_url = subprocess.check_output(['python3', '-m streamlink','--stream-url','--yupptv-boxid=***REMOVED***','--yupptv-yuppflixtoken=***REMOVED***',url],stderr=subprocess.STDOUT)

                            #stream_url = _get_stream(canonical_name)
                        
                            playlist_line2 = "pipe://python3 -m streamlink --stdout --quiet --yupptv-boxid=***REMOVED*** --yupptv-yuppflixtoken=***REMOVED*** "+url+" best"
                            playlist_str += playlist_line1 + "\n" + playlist_line2 + "\n"
                            programmeElem = get_programme(canonical_name,chanid)
                            if(programmeElem is not None or first_run):
                                if programmeElem is not None:
                                    xmlTvRoot.append(programmeElem)
                                xmlTvRoot.append(channelElem)


        epg_str = etree.tostring(xmlTvRoot, pretty_print=True,
                                xml_declaration=True, encoding="UTF-8", doctype=XMLTV_DOCTYPE)

        if(list(os.uname())[0] != "Darwin"):

            xmltv_socket = "/srv/home/hts/.hts/tvheadend/epggrab/xmltv.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

            sock.connect(xmltv_socket)
            sock.sendall(epg_str)

            sock.close()

        with open(xmltv_file, 'wb') as f:
            f.write(epg_str)

        with open(playlist_file, 'w') as f:
            f.write(playlist_str)

        print('wait 60 seconds')
        time.sleep(60)
        first_run = False

def start_data_loop():
    thread_data_loop = threading.Thread(target=_data_refresh_loop, args=())
    thread_data_loop.daemon = False  # Daemonize thread
    thread_data_loop.start()

@app.route("/<name>")
def hello(name):
    return f"Hello, {(name)}!"

# from https://github.com/streamlink/streamlink/blob/master/src/streamlink_cli/main.py#L273
def open_stream(stream):
    """Opens a stream and reads 8192 bytes from it.
    This is useful to check if a stream actually has data
    before opening the output.
    """
    global stream_fd

    # Attempts to open the stream
    try:
        stream_fd = stream.open()
    except StreamError as err:
        raise StreamError(f"Could not open stream: {err}")

    # Read 8192 bytes before proceeding to check for errors.
    # This is to avoid opening the output unnecessarily.
    try:
        logger.debug("Pre-buffering 8192 bytes")
        prebuffer = stream_fd.read(8192)
    except OSError as err:
        stream_fd.close()
        raise StreamError(f"Failed to read data from stream: {err}")

    if not prebuffer:
        stream_fd.close()
        raise StreamError("No data returned from stream")

    return stream_fd, prebuffer

# from https://github.com/streamlink/streamlink/blob/master/src/streamlink_cli/main.py#L338
def read_stream(stream, prebuffer, chunk_size=8192):
    """Reads data from stream and then writes it to the output."""


    stream_iterator = chain(
        [prebuffer],
        iter(partial(stream.read, chunk_size), b"")
    )

    try:
        for data in stream_iterator:
                yield data
    except OSError as err:
        print(f"Error when reading from stream: {err}, exiting")
        os.exit()
    finally:
        stream.close()
        print("Stream ended")


@app.route('/video/<provider>/<channel>')
def video_feed(provider,channel):
    print("got request for %s-%s"%(provider,channel))
    #stream_url = _get_stream(channel,None)
    #stream_url = "https://www.yupptv.com/channels/alai-balai/live"
    #stream_url = "https://www.yupptv.com/channels/colors-uk/live"
    #print(stream_url)
    #streams = streamlink_session.streams(stream_url )  
    streams = _get_stream(channel);
    print(streams)  
    if('best' in streams):
        stream = streams['best']      
    else:
        #workaround as the restored streams aren't a Streams object but a OrderedDict
        stream = streams[list(streams)[0]]
    print(stream)  
    stream_fd, prebuffer = open_stream(stream)
    
    def generate():
       data = ""
    #    while (data = fd.read(1024)):
            
    #    yield data
    return Response(read_stream(stream_fd, prebuffer), mimetype='video/unknown')
    
if __name__ == '__main__':
    http = WSGIServer(('', 5005),
                      app.wsgi_app, log=logger, error_log=logger)
    start_data_loop()
    http.serve_forever()