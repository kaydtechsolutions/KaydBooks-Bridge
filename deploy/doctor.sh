#!/bin/sh
set -eu

if [ -r /etc/kaydbooks/bridge.env ]; then
    set -a
    . /etc/kaydbooks/bridge.env
    set +a
fi
BASE_URL=${KAYDBOOKS_BASE_URL:?KAYDBOOKS_BASE_URL is required}
COMPANY=${KAYDBOOKS_DOCTOR_COMPANY:-}

failed=0
check() {
    if "$@" >/dev/null 2>&1; then
        printf 'PASS %s\n' "$*"
    else
        printf 'FAIL %s\n' "$*"
        failed=1
    fi
}

check_eventually() {
    label=$1
    shift
    attempts=0
    while [ "$attempts" -lt 15 ]; do
        if "$@" >/dev/null 2>&1; then
            printf 'PASS %s\n' "$label"
            return
        fi
        attempts=$((attempts + 1))
        sleep 1
    done
    printf 'FAIL %s\n' "$label"
    failed=1
}

whatsapp_connected() {
    curl --fail --silent --show-error --max-time 3 http://127.0.0.1:3000/health |
        grep -Eq '"status"[[:space:]]*:[[:space:]]*"connected"'
}

hermes_mcp_connected() {
    pgrep -u hermes -f '/opt/kaydbooks/current/bin/kaydbooks-bridge-tools' >/dev/null
}

check test -s /etc/kaydbooks/bridge.env
check test -s /etc/kaydbooks/bridge-config.json
check test -s /etc/kaydbooks/credentials.json
check test -s /etc/kaydbooks/remote-policy.json
check systemctl is-active kaydbooks-bridge.service
check systemctl is-active kaydbooks-remote-mcp.service
check systemctl is-active hermes-gateway.service
check systemctl is-active kaydbooks-hermes-worker.service
check systemctl is-active caddy.service
check systemctl is-active tailscaled.service
check curl --fail --silent --show-error http://127.0.0.1:8080/healthz
check_eventually whatsapp_connected whatsapp_connected
check_eventually hermes_mcp_connected hermes_mcp_connected
HOST=$(printf '%s' "$BASE_URL" | sed -E 's#^https://([^/]+)/?$#\1#')
if [ -z "$HOST" ] || [ "$HOST" = "$BASE_URL" ]; then
    echo 'FAIL invalid KAYDBOOKS_BASE_URL' >&2
    exit 2
fi
check curl --fail --silent --show-error -H "Host: $HOST" http://127.0.0.1:8088/health
DB=$(/opt/kaydbooks/current/bin/python -c \
    'import sys; from kaydbooks_bridge.config import Config; c=Config.load(sys.argv[1]); company=sys.argv[2] or sorted(c.companies)[0]; print(c.root / company / "jobs.sqlite3")' \
    "$KAYDBOOKS_CONFIG" "$COMPANY")
if test -f "$DB"; then
    echo 'PASS company database exists'
else
    echo 'FAIL company database exists'
    failed=1
fi
if sqlite3 "$DB" 'PRAGMA quick_check' | grep -qx ok; then
    echo 'PASS company database quick_check'
else
    echo 'FAIL company database quick_check'
    failed=1
fi
TAILSCALE_IP=$(tailscale ip -4)
check curl --fail --silent --show-error --resolve "$HOST:443:$TAILSCALE_IP" "$BASE_URL/health"
exit "$failed"
