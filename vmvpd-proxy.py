
from gevent import monkey
monkey.patch_all()
from itertools import chain
from functools import partial
from flask import Flask, Response, request, jsonify, abort, render_template
from gevent.pywsgi import WSGIServer

import sys
import os
import socket
import json
import time
import threading
from datetime import timezone, datetime, timedelta
from plugin_yupptv import PluginYuppTV
from streamlink.logger import StreamlinkLogger
from plugin_zattoo import PluginZattoo

import logging
level = "trace"

logging.basicConfig(
        level=level,
        style="{",
        format=("[{asctime}]" if level == "trace" else "") + "[{name}][{levelname}] {message}",
        datefmt="%H:%M:%S" + (".%f" if level == "trace" else "")
    )
logging.setLoggerClass(StreamlinkLogger)

logging.basicConfig(
        level=level,
        style="{",
        format=("[{asctime}]" if level == "trace" else "") + "[{name}][{levelname}] {message}",
        datefmt="%H:%M:%S" + (".%f" if level == "trace" else "")
    )
log = logging.getLogger(__name__)

root = logging.getLogger("streamlink")
root.setLevel("trace")

#########################
# vMVPD Headend
#########################
# A vMVPD proxy and server
#########################

yupptv_settings = dict(boxid="***REMOVED***",
                      yuppflixtoken="***REMOVED***")


zattoo_settings = dict(email="***REMOVED***",
                      password="***REMOVED***")
plugins = {}
plugins["yupptv"] = PluginYuppTV(yupptv_settings)
plugins["zattoo"] = PluginZattoo(zattoo_settings)


# setup a few more things
app = Flask(__name__)

# setup logging
#logging.basicConfig(level=-1)


def _file_age_in_seconds(pathname):
    return time.time() - os.path.getmtime(pathname)



def _data_refresh_loop():
    while True:


        plugins['zattoo'].refresh_channels_playlist()
        #print(plugins['zattoo'].get_channels_playlist())
        #quit()
       # plugins['yupptv'].refresh_channels_playlist()
       # plugins['yupptv'].refresh_epg_xmltv()
       # plugins['yupptv'].refresh_streams()

        log.info('wait 60 seconds')
        time.sleep(60)


def start_data_loop():
    thread_data_loop = threading.Thread(target=_data_refresh_loop, args=())
    thread_data_loop.daemon = False  # Daemonize thread
    thread_data_loop.start()


@app.route("/<name>")
def hello(name):
    return f"Hello, {(name)}!"


def read_stream(stream, prebuffer, chunk_size=16048):
    # from https://github.com/streamlink/streamlink/blob/master/src/streamlink_cli/main.py#L338
    """Reads data from stream and then writes it to the output."""

    stream_iterator = chain(
        [prebuffer],
        iter(partial(stream.read, chunk_size), b"")
    )

    try:
        for data in stream_iterator:
            yield data
    except OSError as err:
        log.info(f"Error when reading from stream: {err}, exiting")
        os.exit()
    finally:
        stream.close()
        log.info("Stream ended")

@app.route('/<provider>/epg.xml')
def channel_epg(provider):
    return Response(plugins[provider].get_epg_xmltv(), mimetype='text/xml');


@app.route('/<provider>/channels.m3u')
def channel_listing(provider):
    return plugins[provider].get_channels_playlist();


@app.route('/<provider>/<channel>')
def video_feed(provider, channel):
    log.info("got stream request for %s-%s" % (provider, channel))

    stream = plugins[provider].get_stream(channel)
    stream_fd, prebuffer = plugins[provider].open_stream(stream)
    log.debug("Writing stream to player")
    return Response(read_stream(stream_fd, prebuffer), mimetype='video/unknown')


if __name__ == '__main__':
    http = WSGIServer(('', 5005),
                      app.wsgi_app, log=log, error_log=log)
    start_data_loop()
    http.serve_forever()
