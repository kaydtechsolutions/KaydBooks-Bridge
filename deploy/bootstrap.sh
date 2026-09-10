#!/bin/sh
# Downloads KB only from its GitHub repository, then launches the installer.
set -eu
if [ "${1:-}" = '--check' ]; then
    failed=0
    for command in git curl python3; do
        if command -v "$command" >/dev/null 2>&1; then
            echo "PASS $command"
        else
            echo "MISSING $command (installation mode installs it)"
            failed=1
        fi
    done
    echo 'Run deploy/auto-install.sh --check from a checkout for the full host check.'
    exit "$failed"
fi
if [ "${1:-}" = '--help' ]; then
    echo 'Usage: sudo sh bootstrap.sh [auto-install.sh options]'
    echo 'KB_REF may be a reviewed Git commit, tag, or branch. Default: main.'
    exit 0
fi
[ "$(id -u)" = 0 ] || { echo 'Run with sudo.' >&2; exit 2; }
. /etc/os-release
case "$ID:$VERSION_ID" in
    debian:13|ubuntu:24.04) ;;
    *) echo 'Supported: Debian 13 or Ubuntu 24.04.' >&2; exit 2 ;;
esac
[ -d /run/systemd/system ] || { echo 'A running systemd is required.' >&2; exit 2; }
KB_REF=${KB_REF:-main}
case "$KB_REF" in
    -*|*[!A-Za-z0-9_./-]*|'') echo 'Invalid KB_REF.' >&2; exit 2 ;;
esac
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git curl ca-certificates python3
KB_SOURCE=$(mktemp -d /tmp/kaydbooks-source.XXXXXXXX)
trap 'rm -rf "$KB_SOURCE"' EXIT HUP INT TERM
git -C "$KB_SOURCE" init -q
git -C "$KB_SOURCE" remote add origin https://github.com/kaydtechsolutions/KaydBooks-Bridge.git
git -C "$KB_SOURCE" fetch --depth=1 origin "$KB_REF"
git -C "$KB_SOURCE" checkout --detach FETCH_HEAD
echo 'Installing this GitHub commit:'
git -C "$KB_SOURCE" rev-parse HEAD
sh "$KB_SOURCE/deploy/auto-install.sh" "$@"
