from vmvpd_plugin import vMVPD_Plugin


import requests
import re
import os
import json
import logging
from lxml import etree
from lxml.etree import HTMLParser
from lxml.cssselect import CSSSelector
from streamlink.plugin.api.http_session import HTTPSession
from streamlink.plugin.api import useragents
from streamlink_cli.utils import JSONEncoder
from streamlink import NoPluginError, PluginError, StreamError, Streamlink, __version__ as streamlink_version

from urllib.parse import urlparse, parse_qs
from datetime import timezone, datetime, timedelta
html_parser = HTMLParser()
logger = logging.getLogger()


class CachedHTTPSession(HTTPSession):
    all_streams = {}

    def request(self, method, url, *args, **kwargs):
        # check if this is a request for the html page so we can directly return the manifest url if it's still valid
        channel_regex = r'https?://(?:www\.)?yupptv\.com/channels/([a-z\-]+)/live'
        match = re.match(channel_regex, url)
        if match:
            channel_name = match.group(1)
            if(channel_name in self.all_streams and self.all_streams[channel_name]["expiry"] > datetime.now().timestamp()+600):
                fakeResponse = Response()
                fakeResponse.text = "streamUrl=[{src: \"" + \
                    self.all_streams[channel_name]["url"] + \
                    "\" , title: '', description: ''}]"
                return fakeResponse
            else:
                logger.info("%s fetch tv page for real" % channel_name)
        return super().request(method, url, *args, **kwargs)


class CachedStreamlink(Streamlink):
    def __init__(self, options=None):
        super().__init__(options)
        self.http = CachedHTTPSession()


class vMVPD_YuppTV(vMVPD_Plugin):

    # restore the streams from json
    all_streams = {}
    streams_file = "streams.json"
    cookies = {}
    streamlink_session = None

    def __init__(self, cookies):
        super().__init__()

        self.streamlink_session = CachedStreamlink()
        if os.path.isfile(self.streams_file):
            json_file = open(self.streams_file)
            self.all_streams = json.load(json_file)

        # init StreamLink
        self.cookies = cookies

        self.streamlink_session.set_plugin_option(
            "yupptv", "boxid", cookies["BoxId"])
        self.streamlink_session.set_plugin_option(
            "yupptv", "yuppflixtoken", cookies["YuppflixToken"])
        self.streamlink_session.set_option("hls-live-edge", 1)

    def update_all_streams_cache(self, all_streams):
        self.all_streams = all_streams
        self.streamlink_session.http.all_streams = all_streams

        with open(self.streams_file, 'w') as outfile:
            json.dump(self.all_streams, outfile,
                      cls=JSONEncoder, sort_keys=True, indent=4)

    # refresh the streams for a specific channel if they are outdated. Returns none if nothing was updated
    def _refresh_streams(self, channel_name):
        if(channel_name not in self.all_streams or self.all_streams[channel_name]["expiry"] < datetime.now().timestamp()+600):
            logger.info("%s refresh streams" % channel_name)
            return self._get_streams(channel_name)
        else:
            logger.info("%s streams still current" % channel_name)
            return None

    # get the streams for a specific channel
    def _get_streams(self, channel_name):
        # todo: take this from the epg/channels crawler
        page_url = "https://www.yupptv.com/channels/"+channel_name+"/live"
        channel_streams = self.streamlink_session.streams(page_url)
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
        self.all_streams[channel_name] = {
            "url": stream_url,
            "streams": channel_streams,
            "expiry": expiry
        }
        self.update_all_streams_cache(self.all_streams)
        return channel_streams

    def get_channel_language(self, channel_name):
        epg_file = "epg.json"
        epg = {}
        if os.path.isfile(epg_file):
            json_file = open(epg_file)
            epg = json.load(json_file)
        if channel_name in epg:
            return epg[channel_name]['language']
        else:
            return ""

    # todo: we should know the url here
    def get_epg_programme(self, channel_name, first_run):

        page_url = "https://www.yupptv.com/channels/"+channel_name+"/live"
        filename = channel_name+".html"
        self._downloadFile("tmp/"+filename, page_url)
        raw = open("tmp/"+filename, "r").read()
        chanid = re.search('chanid = \'(\d+)\'', raw).group(1)
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
            r = requests.get(url,  cookies=self.cookies)
            epg_data = r.json()[0]

            endTime = datetime.fromtimestamp(0)
            if (len(epg_data['Programs']) > 0):
                programme = epg_data['Programs'][0]
                endTime = datetime.fromtimestamp(
                    int(programme['endTime'])/1000)

            epg[channel_name] = epg_data
            with open(epg_file, 'w') as outfile:
                json.dump(epg, outfile, sort_keys=True, indent=4)
        else:
            epg_data = epg[channel_name]

        dummyTitle = "Program@"
        if(len(epg_data['Programs']) > 0 and epg_data['Programs'][0]['name'][0:len(dummyTitle)] != dummyTitle):
            programme = epg_data['Programs'][0]
            logger.info("Refreshing EPG for %s" % channel_name)
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

    def get_all_channels(self):
        channelNo = 0
        channels = {}

        url = 'https://www.yupptv.com/livetv'
        filename = "index.html"
        self._downloadFile("tmp/"+filename, url)
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
                        self._downloadFile("tmp/"+filename, url)

                    else:
                        url = "https://www.yupptv.com/livetv/sectionGetMore/" + \
                            section+"/"+str(last_index)
                        filename = "index"+section+"-"+str(last_index)+".html"
                        self._downloadFile("tmp/"+filename, url)

                    raw = open("tmp/"+filename, "r").read()

                    root = etree.fromstring(raw, html_parser)
                    paging_selector = CSSSelector('div.last-index')
                    count = int(paging_selector(root)[0].get('data-count'))
                    last_index = int(paging_selector(
                        root)[0].get('data-last-index'))

                    logger.info("------------------")
                    logger.info("%s page %i, count %i, last index %i" %
                                (section, page, count, last_index))
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
                        canonical_name = url[len(
                            "https://www.yupptv.com/channels/"):-len("/live")]
                        if(available and canonical_name not in channels):

                            channelNo += 1

                            def rchop(s, suffix):
                                if suffix and s.endswith(suffix):
                                    return s[:-len(suffix)]
                                return s
                            display_name = rchop(img.get('alt'), ' Online')

                            language = self.get_channel_language(
                                canonical_name)
                            channels[canonical_name] = {
                                "display_name": display_name, "channelNo": channelNo, "logo": logo, "group": section_name+";"+language, "url": url}
        return channels
