#!/bin/bash
# Server copies only: stop the server, then wait for cron (or whatever keeps it running) to start it again.
DIR=$(dirname "$(readlink -f "$0")")
PORT=$(python3 -c "import json; print(json.load(open('$DIR/config.json'))['port'])")
PAT="^[^ ]*python3 $DIR/server.py"
pkill -f "$PAT"
timeout 20 bash -c "while pgrep -f '$PAT' >/dev/null; do :; done"
curl -s --retry 90 --retry-all-errors --retry-delay 2 --retry-max-time 175 -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/api/status"
