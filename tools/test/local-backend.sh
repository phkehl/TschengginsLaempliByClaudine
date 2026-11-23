#!/bin/sh
# Start a local test backend: local-backend.sh <dir> [<port>]
#
# Writes <dir>/test.yaml (127.0.0.1:<port>, default 8099, WebSocket server on <port> + 1, data in <dir>/data, GitHub
# secret "localtest", generic token "localtok", editor "editor", device editor "deveditor" for chlotz-0000aa) and runs
# the backend in the foreground (Ctrl-C stops it). Use a new directory, not the real data. Page:
# http://127.0.0.1:<port>/web/devices (read only: no X-Forwarded-User header without proxy)

set -eu
if [ $# -lt 1 ]; then
    echo "Usage: $0 <dir> [<port>]" >&2
    exit 2
fi
dir=$(realpath "$1")
port=${2:-8099}
top=$(dirname "$(dirname "$(dirname "$(realpath "$0")")")")
mkdir -p "$dir/data"
cat > "$dir/test.yaml" <<EOF
listen: 127.0.0.1
port: $port
data: $dir/data
log_level: info
github_webhook_secrets: [localtest]
generic_tokens: [localtok]
editors: [editor]
device_editors:
    deveditor: [chlotz-0000aa]
EOF
exec /usr/bin/python3 "$top/backend/tschenggins-laempli.py" --config "$dir/test.yaml"
