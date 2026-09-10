#!/bin/sh
set -eu

VERSION=0.2.0
if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 2
fi
if [ "$#" -ne 2 ]; then
    echo "usage: install.sh WHEEL SHA256" >&2
    exit 2
fi
WHEEL=$1
EXPECTED=$2
case "$EXPECTED" in
    *[!a-f0-9]*|'') echo "lowercase SHA256 required" >&2; exit 2 ;;
esac
if [ "${#EXPECTED}" -ne 64 ] || [ ! -f "$WHEEL" ]; then
    echo "wheel and SHA256 required" >&2
    exit 2
fi
ACTUAL=$(sha256sum "$WHEEL" | cut -d ' ' -f 1)
if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "wheel checksum mismatch" >&2
    exit 2
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates caddy sqlite3 curl

getent group kaydbooks >/dev/null || groupadd --system kaydbooks
id kaydbooks >/dev/null 2>&1 || useradd --system --gid kaydbooks \
    --home-dir /var/lib/kaydbooks --shell /usr/sbin/nologin kaydbooks
getent group hermes >/dev/null || groupadd --system hermes
id hermes >/dev/null 2>&1 || useradd --system --gid hermes \
    --home-dir /var/lib/hermes --shell /usr/sbin/nologin hermes
usermod -a -G kaydbooks hermes
id kaydbooks-tunnel >/dev/null 2>&1 || useradd --system --gid kaydbooks \
    --home-dir /var/lib/kaydbooks-tunnel --shell /usr/sbin/nologin kaydbooks-tunnel
install -d -o root -g kaydbooks -m 0750 /etc/kaydbooks
install -d -o kaydbooks -g kaydbooks -m 2770 /var/lib/kaydbooks /var/log/kaydbooks
install -d -o hermes -g hermes -m 0700 /var/lib/hermes
install -d -o kaydbooks-tunnel -g kaydbooks -m 0750 /var/lib/kaydbooks-tunnel
install -d -o root -g root -m 0755 /opt/kaydbooks/releases

SHORT=$(printf '%.12s' "$EXPECTED")
RELEASE=/opt/kaydbooks/releases/$VERSION-$SHORT
if [ -e "$RELEASE" ]; then
    test "$(cat "$RELEASE/.wheel.sha256")" = "$EXPECTED"
    "$RELEASE/bin/python" -c 'import kaydbooks_bridge; assert kaydbooks_bridge.__version__ == "0.2.0"'
else
    # venv entry-point shebangs contain the absolute creation path, so build the
    # checksum-unique release in place and remove it if installation fails.
    trap 'rm -rf "$RELEASE"' EXIT HUP INT TERM
    python3 -m venv "$RELEASE"
    "$RELEASE/bin/pip" install --disable-pip-version-check "$WHEEL[server,remote,intake]"
    "$RELEASE/bin/python" -c 'import kaydbooks_bridge; assert kaydbooks_bridge.__version__ == "0.2.0"'
    printf '%s\n' "$EXPECTED" > "$RELEASE/.wheel.sha256"
    trap - EXIT HUP INT TERM
fi
ln -sfn "$RELEASE" /opt/kaydbooks/current

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/kaydbooks-bridge.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/kaydbooks-remote-mcp.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/kaydbooks-openai-tunnel.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/hermes-gateway.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/kaydbooks-hermes-worker.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SCRIPT_DIR/Caddyfile" /etc/caddy/Caddyfile
install -o root -g root -m 0755 "$SCRIPT_DIR/doctor.sh" /usr/local/sbin/kaydbooks-doctor
if [ ! -e /etc/kaydbooks/bridge.env ]; then
    systemctl disable --now caddy.service >/dev/null 2>&1 || true
    install -o root -g kaydbooks -m 0640 "$SCRIPT_DIR/bridge.env.example" /etc/kaydbooks/bridge.env
fi
if [ ! -e /etc/kaydbooks/remote-policy.json ]; then
    install -o root -g kaydbooks -m 0640 "$SCRIPT_DIR/remote-policy.example.json" /etc/kaydbooks/remote-policy.json
fi
if [ ! -e /etc/kaydbooks/composio-policy.json ]; then
    install -o root -g kaydbooks -m 0640 "$SCRIPT_DIR/composio-policy.example.json" /etc/kaydbooks/composio-policy.json
fi
systemctl daemon-reload
caddy validate --config /etc/caddy/Caddyfile
echo "installed KaydBooks Bridge $VERSION; configure private files before enabling services"
