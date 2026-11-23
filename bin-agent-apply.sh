#!/bin/bash
# Server copies only (the desktop program restarts itself through basis.pyw). Restart after the Customize chat changed
# server code, or undid such a change. If the server does not come back, put the files back as they were before
# change $1 and let cron start the old code again.
DIR=$(dirname "$(readlink -f "$0")")
cd "$DIR" || exit 1
SNAP="$1"
PORT=$(python3 -c "import json; print(json.load(open('config.json'))['port'])")
PAT="^[^ ]*python3 $DIR/server.py"
echo "$(date '+%F %T') apply $SNAP"
if ! python3 -c "import sys
for p in sys.argv[1:]: compile(open(p, encoding='utf-8').read(), p, 'exec')" server.py fin/*.py; then
  python3 -m fin.agent undo "$SNAP" failed
  exit 1
fi
pkill -f "$PAT"
timeout 20 bash -c "while pgrep -f '$PAT' >/dev/null; do sleep 0.2; done"
code=$(curl -s --retry 80 --retry-all-errors --retry-delay 2 --retry-max-time 170 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/status")
if [ "$code" != "200" ]; then
  echo "$(date '+%F %T') server did not come back ($code); undoing $SNAP"
  python3 -m fin.agent undo "$SNAP" failed
  pkill -f "$PAT"
  exit 1
fi
echo "$(date '+%F %T') applied $SNAP"
