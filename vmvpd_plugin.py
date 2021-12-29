import requests
import re
import time
import os


MAX_FILE_AGE = 48*60*60


class vMVPD_Plugin:

    def __init__(self):
        return

        

    def _file_age_in_seconds(self,pathname):
        return time.time() - os.path.getmtime(pathname)


    def _downloadFile(self, filename, url):
        global downloadCount
        if (not os.path.isfile(filename) or self._file_age_in_seconds(filename) > MAX_FILE_AGE):

            r = requests.get(url,  cookies=cookies)
            open(filename, 'wb').write(r.content)
