vMVPD-Proxy
========

A small flask app to feed various [vMVPD](https://www.thewrap.com/vmvpd-svod-avod-tvod-streaming-market-explained/)s into Tvheadend. Supported Providers:

- YuppTV ✅
- [Zattoo](https://zattoo.com/) (WIP ⏳)
- [PlutoTV](https://pluto.tv/) (WIP ⏳)
- [Init7 TV7](https://www.init7.net/en/tv/) (WIP ⏳)

Once configured, the proxy provides a m3u playist for each provider and generates XMLTV schedules (and pushes them directly into tvheadend if requested). The proxy provides MPEG-TS feeds with minimal latency.

### Setup & Configuration

1. Installation: See ```install.sh ```or manually create a virtual environment and install the requirements from ```requirements.txt```
2. Configuration: Add a ```.env``` file for your configuration variables:  ```YUPPTV_BOXID```, ```YUPPTV_YUPPFLIXTOKEN```, ```ZATTOO_EMAIL```, ```ZATTOO_PASSWORD``` 
4. Running: See ```run.sh``` or manually activate the virtual environment and launch ```python3 vmvpd-proxy.py```



### Access the lineup
The proxy provides 3 endpoints:

- ```/<provider>/channels.m3u```: The full listing of the selected provider, e.g. ```http://localhost:5005/yupptv/channels.m3u```
- ```/<provider>/epg.xml```: A EPG for all channels. This might only include currently running programs for some providers which don't provide a program guide (e.g. YuppTV). e.g. ```http://localhost:5005/yupptv/epg.xml```
- ```/<provider>/<channel>```: A MPEG-TS-formatted stream of the selected channel and provider - e.g. ```http://localhost:5005/yupptv/colors-uk```