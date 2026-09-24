#!/bin/sh
# Container entrypoint: start the bgutil PO token provider in the background, then the web app.
set -eu

POT_MAIN=/opt/bgutil/server/build/main.js
POT_URL=http://127.0.0.1:4416/ping

if command -v node >/dev/null 2>&1 && [ -f "$POT_MAIN" ]; then
    node "$POT_MAIN" --host 127.0.0.1 --port 4416 &
    ready=0
    i=0
    while [ "$i" -lt 20 ]; do
        if python -c "import sys, urllib.request as u; u.build_opener(u.ProxyHandler({})).open(sys.argv[1], timeout=1)" "$POT_URL" >/dev/null 2>&1; then
            ready=1
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if [ "$ready" -eq 1 ]; then
        echo "start.sh: PO token provider is up at $POT_URL"
    else
        echo "start.sh: PO token provider did not answer at $POT_URL within 20 s; continuing without it"
    fi
else
    echo "start.sh: PO token provider not installed ($POT_MAIN); continuing without it"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-7860}"
