import requests
import re
import time
import os
import json
from plugin_vmvpd import PluginVMVPD
import logging
logger = logging.getLogger()

from streamlink_cli.utils import JSONEncoder
MAX_FILE_AGE = 48*60*60


from lxml import etree

XMLTV_DOCTYPE = '<!DOCTYPE tv SYSTEM "https://github.com/XMLTV/xmltv/raw/master/xmltv.dtd">'

class FakeResponse(requests.Response):
    def setText(self,string):
        self._content = bytes(string, 'utf-8')
        self.encoding = 'utf-8'

class PluginSteamlink(PluginVMVPD):
    
    settings = {}
    streamlink_session = None
    all_streams = {}

    def __init__(self,settings):
        super().__init__()
        self.streams_file = "tmp/"+self.getName()+"-streams.json"
        
        if os.path.isfile(self.streams_file):
            json_file = open(self.streams_file)
            self.all_streams = json.load(json_file)

        # init StreamLink
        self.settings = settings
        
        for name in settings:
            self.streamlink_session.set_plugin_option(
                self.getName(),name, settings[name])
                
        self.streamlink_session.set_option("hls-live-edge", 1)

    

    def update_all_streams_cache(self, all_streams):
        self.all_streams = all_streams
        self.streamlink_session.http.all_streams = all_streams

        with open(self.streams_file, 'w') as outfile:
            json.dump(self.all_streams, outfile,
                      cls=JSONEncoder, sort_keys=True, indent=4)
