#!/bin/sh
# Registers the app as a macOS LaunchAgent so it starts at login and restarts if it crashes.
#
#   cd youtube-subtitles && scripts/mac/install_launch_agent.sh <MCP_API_KEY> [port] [PUBLIC_BASE_URL]
#
# PUBLIC_BASE_URL is the public address of the app, for example the Tailscale Funnel URL
# https://<mac-name>.<tailnet>.ts.net. With it, the MCP tool get_download_link returns full links.
#
# Logs: ~/Library/Logs/youtube-subtitles.log
# Remove: launchctl bootout gui/$(id -u)/com.youtube-subtitles && rm ~/Library/LaunchAgents/com.youtube-subtitles.plist
set -eu

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <MCP_API_KEY> [port] [PUBLIC_BASE_URL]" >&2
    exit 1
fi
MCP_API_KEY=$1
PORT=${2:-7860}
PUBLIC_BASE_URL=${3:-}
PUBLIC_BASE_URL_ENTRY=""
if [ -n "$PUBLIC_BASE_URL" ]; then
    case "$PUBLIC_BASE_URL" in
        http://*|https://*) ;;
        *) echo "PUBLIC_BASE_URL must start with http:// or https://" >&2; exit 1 ;;
    esac
    PUBLIC_BASE_URL=${PUBLIC_BASE_URL%/}
    PUBLIC_BASE_URL_ENTRY="<key>PUBLIC_BASE_URL</key><string>$PUBLIC_BASE_URL</string>"
fi

APP_DIR=$(cd "$(dirname "$0")/../.." && pwd)
UVICORN="$APP_DIR/.venv/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
    echo "uvicorn not found at $UVICORN. Create the virtualenv first (see README)." >&2
    exit 1
fi

LABEL=com.youtube-subtitles
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/youtube-subtitles.log"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$UVICORN</string>
        <string>app.main:app</string>
        <string>--host</string><string>127.0.0.1</string>
        <string>--port</string><string>$PORT</string>
    </array>
    <key>WorkingDirectory</key><string>$APP_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MCP_API_KEY</key><string>$MCP_API_KEY</string>
        $PUBLIC_BASE_URL_ENTRY
        <key>PATH</key><string>$APP_DIR/.venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
chmod 600 "$PLIST"

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# bootout returns before launchd has removed the old job; bootstrapping the same label too early fails
# with "Bootstrap failed: 5: Input/output error". Wait for the label to disappear, then retry a few times.
i=0
while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && [ "$i" -lt 20 ]; do
    sleep 0.5
    i=$((i + 1))
done
i=0
until launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 5 ]; then
        echo "launchctl bootstrap keeps failing. Run it once more by hand:" >&2
        echo "  launchctl bootstrap $DOMAIN $PLIST && launchctl kickstart -k $DOMAIN/$LABEL" >&2
        exit 1
    fi
    sleep 1
done
launchctl kickstart -k "$DOMAIN/$LABEL"

sleep 2
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null; then
    echo "youtube-subtitles is running on http://127.0.0.1:$PORT and will start at login."
else
    echo "The agent was registered but the app is not answering yet. Check $LOG" >&2
    exit 1
fi
