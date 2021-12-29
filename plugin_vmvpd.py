import requests
import re
import time
import os

import logging
logger = logging.getLogger()

MAX_FILE_AGE = 48*60*60


from lxml import etree

XMLTV_DOCTYPE = '<!DOCTYPE tv SYSTEM "https://github.com/XMLTV/xmltv/raw/master/xmltv.dtd">'

class FakeResponse(requests.Response):
    def setText(self,string):
        self._content = bytes(string, 'utf-8')
        self.encoding = 'utf-8'

class PluginVMVPD:
    streams_file = ""
    def __init__(self):
        return

    def getName(self):
        raise "not implemented"

    def _file_age_in_seconds(self,pathname):
        return time.time() - os.path.getmtime(pathname)


    def _downloadFile(self, filename, url):
        global downloadCount
        if (not os.path.isfile(filename) or self._file_age_in_seconds(filename) > MAX_FILE_AGE):

            r = requests.get(url,  cookies=cookies)
            open(filename, 'wb').write(r.content)

    def get_stream(self,channel):
        streams =self._get_streams(channel)
        return streams['best']
        


    def refresh_channels_playlist(self):
        logger.info("Recreating playlist for %s" % self.getName())
        channels = self.get_all_channels()

        playlist_file = "tmp/"+self.getName()+".m3u"
        playlist_str = "#EXTM3U\n"

        for canonical_name in channels:
            display_name = channels[canonical_name]["display_name"]
            channelNo = channels[canonical_name]["channelNo"]
            logo = channels[canonical_name]["logo"]
            group = channels[canonical_name]["group"]
            url = channels[canonical_name]["url"]
            logger.info("%i %s" % (channelNo, canonical_name))

            # update m3u playlist and refresh streams  (to enable faster zapping)
            playlist_line1 = "#EXTINF:-1 tvh-epg=\"disable\"  tvh-chnum=\"" + \
                str(channelNo)+"\" tvg-logo=\""+logo+"\" tvg-name=\""+canonical_name + \
                "\" group-title=\""+group+"\", "+display_name
            playlist_line2 = "http://localhost:5005/"+self.getName()+"/"+canonical_name
            playlist_str += playlist_line1 + "\n" + playlist_line2 + "\n"

        with open(playlist_file, 'w') as f:
            f.write(playlist_str)

        return playlist_str

    def get_channels_playlist(self):
        logger.info("Getting playlist for %s" % self.getName())
        playlist_file = "tmp/"+self.getName()+".m3u"
        if (not os.path.isfile(playlist_file) or self._file_age_in_seconds(playlist_file) > 60*60):
            return self.refresh_channels_playlist(self.getName())
        else:
            return open(playlist_file, "r").read()


    def get_epg_xmltv(self):
        ''' returns whole epg '''
        xmltv_file = "tmp/"+self.getName()+".xml"
        if (not os.path.isfile(xmltv_file) or self._file_age_in_seconds(xmltv_file) > 60*60):
            return self.refresh_epg_xmltv();
        else:
            return open(xmltv_file, "r").read()

    def refresh_epg_xmltv(self):
        ''' only returns updated channels'''
        channels = self.get_all_channels()
        logger.info("refresh_epg")
        xmltv_file = "tmp/"+self.getName()+".xml"
        xmlTvRoot = etree.Element("tv")
        xmlTvRootNew = etree.Element("tv")

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
            programmeElem, refreshed = self.get_epg_programme(
                canonical_name)
        
            if(programmeElem is not None or refreshed):
                if programmeElem is not None:
                    xmlTvRootNew.append(programmeElem)
                xmlTvRootNew.append(channelElem)
           
            if(programmeElem is not None ):
                xmlTvRoot.append(programmeElem)
            xmlTvRoot.append(channelElem)
            
        new_epg_str = etree.tostring(xmlTvRootNew, pretty_print=True,
                                    xml_declaration=True, encoding="UTF-8", doctype=XMLTV_DOCTYPE)
        epg_str = etree.tostring(xmlTvRoot, pretty_print=True,
                                    xml_declaration=True, encoding="UTF-8", doctype=XMLTV_DOCTYPE)
        
        if(list(os.uname())[0] != "Darwin"):

            xmltv_socket = "/srv/home/hts/.hts/tvheadend/epggrab/xmltv.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

            sock.connect(xmltv_socket)
            sock.sendall(new_epg_str)

            sock.close()

        with open(xmltv_file, 'wb') as f:
            f.write(epg_str)

        return epg_str


    def refresh_streams(self):
        ''' refresh variant playlists / streams to ensure fast zapping '''

        channels = self.get_all_channels()

        for canonical_name in channels:
            # update m3u playlist and refresh streams  (to enable faster zapping)
            self._refresh_streams(canonical_name)




    def open_stream(self,stream):
        # from https://github.com/streamlink/streamlink/blob/master/src/streamlink_cli/main.py#L273
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

        # Read 1024 bytes before proceeding to check for errors.
        # This is to avoid opening the output unnecessarily.
        try:
            logger.debug("Pre-buffering 1024 bytes")
            prebuffer = stream_fd.read(1024)
        except OSError as err:
            stream_fd.close()
            raise StreamError(f"Failed to read data from stream: {err}")

        if not prebuffer:
            stream_fd.close()
            raise StreamError("No data returned from stream")

        return stream_fd, prebuffer
