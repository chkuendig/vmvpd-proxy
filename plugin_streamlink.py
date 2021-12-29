import requests
import re
import time
import os
from plugin_vmvpd import PluginVMVPD
import logging
logger = logging.getLogger()

MAX_FILE_AGE = 48*60*60


from lxml import etree

XMLTV_DOCTYPE = '<!DOCTYPE tv SYSTEM "https://github.com/XMLTV/xmltv/raw/master/xmltv.dtd">'

class FakeResponse(requests.Response):
    def setText(self,string):
        self._content = bytes(string, 'utf-8')
        self.encoding = 'utf-8'

class PluginSteamlink(PluginVMVPD):
    
    def __init__(self):
        super().__init__()