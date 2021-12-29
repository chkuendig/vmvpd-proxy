
from vmvpd_yupptv import vMVPD_YuppTV
from lxml import etree
from datetime import timezone, datetime, timedelta
import threading
import time
import json
import socket
import os
import logging
from gevent.pywsgi import WSGIServer
from flask import Flask, Response, request, jsonify, abort, render_template
from functools import partial
from itertools import chain
from gevent import monkey

monkey.patch_all()


yupptv_cookies = dict(BoxId="***REMOVED***",
                      YuppflixToken="***REMOVED***")
plugins = {}
plugins["yupptv"] = vMVPD_YuppTV(yupptv_cookies)


# setup a few more things
app = Flask(__name__)
playlist_file = "yupptv.m3u"
xmltv_file = "epg.xml"
first_run = False
XMLTV_DOCTYPE = '<!DOCTYPE tv SYSTEM "https://github.com/XMLTV/xmltv/raw/master/xmltv.dtd">'

# setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def _data_refresh_loop():
    global first_run
    first_run = True
    while True:

        channels = plugins['yupptv'].get_all_channels()
        playlist_str = "#EXTM3U\n"
        xmlTvRoot = etree.Element("tv")

        for canonical_name in channels:
            display_name = channels[canonical_name]["display_name"]
            channelNo = channels[canonical_name]["channelNo"]
            logo = channels[canonical_name]["logo"]
            group = channels[canonical_name]["group"]
            url = channels[canonical_name]["url"]
            logger.info("%i %s" % (channelNo, canonical_name))

            # put together channel epg
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

            # get programme epg

            programmeElem = plugins['yupptv'].get_epg_programme(
                canonical_name, first_run)
            if(programmeElem is not None or first_run):
                if programmeElem is not None:
                    xmlTvRoot.append(programmeElem)
                xmlTvRoot.append(channelElem)

            # update m3u playlist and refresh streams  (to enable faster zapping)
            plugins['yupptv']._refresh_streams(canonical_name)
            playlist_line1 = "#EXTINF:-1 tvh-epg=\"disable\"  tvh-chnum=\"" + \
                str(channelNo)+"\" tvg-logo=\""+logo+"\" tvg-name=\""+canonical_name + \
                "\" group-title=\""+group+"\", "+display_name
            playlist_line2 = "http://localhost:5005/video/yupptv/"+canonical_name
            playlist_str += playlist_line1 + "\n" + playlist_line2 + "\n"

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

        logger.info('wait 60 seconds')
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
        logger.info(f"Error when reading from stream: {err}, exiting")
        os.exit()
    finally:
        stream.close()
        logger.info("Stream ended")


@app.route('/video/<provider>/<channel>')
def video_feed(provider, channel):
    logger.info("got request for %s-%s" % (provider, channel))
    streams = plugins[provider]._get_streams(channel)
    if('best' in streams):
        stream = streams['best']
    stream_fd, prebuffer = open_stream(stream)

    return Response(read_stream(stream_fd, prebuffer), mimetype='video/unknown')


if __name__ == '__main__':
    http = WSGIServer(('', 5005),
                      app.wsgi_app, log=logger, error_log=logger)
    start_data_loop()
    http.serve_forever()
