source .venv/bin/activate 

if [ ! -L "${HOME}/Library/Application Support/streamlink/plugins/yupptv.py" ]; then
    echo "File not found!"
    ln -s streamlink/src/streamlink/plugins/yupptv.py "${HOME}/Library/Application Support/streamlink/plugins/yupptv.py" 
    exit
fi


python3 channels.py > streamlink.m3u
rm liveproxy.m3u
liveproxy --file streamlink.m3u --file-output liveproxy.m3u
liveproxy