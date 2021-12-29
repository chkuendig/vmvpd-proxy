
from plugin_streamlink import PluginSteamlink, FakeResponse

from streamlink.plugin.api import validate
from streamlink.plugins.zattoo import Zattoo
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
from streamlink import NoPluginError, PluginError, StreamError, Streamlink, __version__ as streamlink_version

from urllib.parse import urlparse, parse_qs
from datetime import timezone, datetime, timedelta
html_parser = HTMLParser()
log = logging.getLogger(__name__)


class CachedHTTPSession(HTTPSession):
    all_streams = {}

    def request(self, method, url, *args, **kwargs):
        # check if this is a request for the html page so we can directly return the manifest url if it's still valid
        channel_regex = r'https?://(?:www\.)?zattoo\.com/channels/?channel=([a-z\-]+)'
        match = re.match(channel_regex, url)
        if match:
            channel_name = match.group(1)
            if(channel_name in self.all_streams and self.all_streams[channel_name]["expiry"] > datetime.now().timestamp()+600):
                fakeResponse = FakeResponse()
                fakeResponse.setText("streamUrl=[{src: \"" +
                                     self.all_streams[channel_name]["url"] +
                                     "\" , title: '', description: ''}]")
                return fakeResponse
            else:
                log.info("%s fetch tv page for real" % channel_name)
        return super().request(method, url, *args, **kwargs)


class CachedStreamlink(Streamlink):
    def __init__(self, options=None):
        super().__init__(options)
        self.http = CachedHTTPSession()


class PluginZattoo(PluginSteamlink):

    def getName(self):
        return 'zattoo'

    # restore the streams from json
    all_streams = {}
    streams_file = ""
    settings = {}
    zattoo_plugin = None

    def __init__(self, settings):
        self.streamlink_session = Streamlink()
        settings['stream-types'] = Zattoo.STREAMS_ZATTOO
        super().__init__(settings)
        self.zattoo_plugin = self.streamlink_session.get_plugins(
        )['zattoo']("https://zattoo.com/channels?channel=test")
        self.zattoo_plugin._hello()
        self.zattoo_plugin._login(settings['email'], settings['password'])
        assert(self.zattoo_plugin._authed)
       # quit()
#            self._hello()
 #           self._login(email, password)
  #      assert(self._authed)

    # refresh the streams for a specific channel if they are outdated. Returns none if nothing was updated
    def _refresh_streams(self, channel_name):
        if(channel_name not in self.all_streams or self.all_streams[channel_name]["expiry"] < datetime.now().timestamp()+600):
            log.info("%s refresh streams" % channel_name)
            return self._get_streams(channel_name)
        else:
            log.debug("%s streams still current" % channel_name)
            return None

    # get the streams for a specific channel
    def _get_streams(self, channel_name):
        try:

            # todo: take this from the epg/channels crawler
            page_url = "https://zattoo.com/channels?channel="+channel_name
            print(page_url)
            log.info("call streamlink")
            channel_streams = self.streamlink_session.streams(page_url)
            print("channel_streams")
            print(channel_streams)
            stream_url = channel_streams['best'].to_manifest_url()
            if(stream_url == None):
                stream_url = channel_streams['best'].url

            # get expiry
            parsed_uri = urlparse(stream_url)
            params = parse_qs(parsed_uri.query)
            expiry = -1
            if("hdnts" in params):
                hdnts = dict(s.split('=')
                             for s in params["hdnts"][0].split("~"))
                expiry = int(hdnts["exp"])
            elif("hdntl" in params):
                hdntl = dict(s.split('=')
                             for s in params["hdntl"][0].split("~"))
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
        except StreamError as e:
            raise e
            log.error(e)
            quit()

    last_lineup_refresh = 0
    channel_lineup = {}

    def get_all_channels(self):
        if(self.last_lineup_refresh < datetime.now().timestamp()-3600):
            # mostly copied from  _get_params_cid in https://github.com/streamlink/streamlink/blob/master/src/streamlink/plugins/zattoo.py
            log.debug('refresh all channel IDs for {0}'.format(self.getName()))
            res = self.zattoo_plugin.session.http.get(
                f'{self.zattoo_plugin.base_url}/zapi/v3/cached/{self.zattoo_plugin._session_attributes.get("power_guide_hash")}/channels',
                headers=self.zattoo_plugin.headers,
                params={'details': 'False'}
            )
            # todo: figure out how to get english groups
            data = res.json()
            groups = data['groups']
            print(len(data['channels']))
            channels = list(
                filter(
                    lambda channel:
                    len(
                        list(filter(
                            lambda quality: quality['availability'] == "available", channel['qualities']))
                    ) > 0,
                    data['channels']
                )
            )

            channel_list = {}
            # loop over channels
            for channel in channels:
                #channel = channels[idx]
                canonical_name = channel['display_alias']
                display_name = channel['title']
                channelNo = channel['number']
                logo = "https://images.zattic.com/logos/%s/black/140x80.png" % (
                    channel['qualities'][0]['logo_token'])
                section_name = groups[channel['group_index']]['name']
                url = "https://zattoo.com/channels?channel=%s" % canonical_name
                channel_list[canonical_name] = {
                    "display_name": display_name, "channelNo": channelNo, "logo": logo, "group": section_name, "url": url}

            print(channel_list)
            self.channel_lineup = channel_list
        return self.channel_lineup



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
    def get_epg_programme(self, channel_name):

        page_url = "https://www.zattoo.com/channels/"+channel_name+"/live"
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
            r = requests.get(url,  settings=self.settings)
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
            log.info("Refreshing EPG for %s" % channel_name)
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

            return programmeElem, refreshed

        return None, False
