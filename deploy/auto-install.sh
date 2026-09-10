#!/bin/sh
# Run from a reviewed GitHub checkout. --check never installs packages.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            echo 'Usage: sudo sh deploy/auto-install.sh [--check] [--yes] [--company company-a]'
            echo '       [--components core,chatgpt,claude,gemini,hermes,composio]'
            echo 'Run on Debian 13 or Ubuntu 24.04, directly or inside an existing LXC.'
            exit 0 ;;
    esac
done
CHECK=0
for arg in "$@"; do
    [ "$arg" != '--check' ] || CHECK=1
done
if ! command -v python3 >/dev/null 2>&1; then
    if [ "$CHECK" = 1 ]; then
        echo 'FAIL python3 missing. Installation mode can install it.'
        exit 1
    fi
    [ "$(id -u)" = 0 ] || { echo 'Run with sudo.' >&2; exit 2; }
    [ -f /etc/os-release ] || exit 2
    . /etc/os-release
    case "$ID:$VERSION_ID" in
        debian:13|ubuntu:24.04) ;;
        *) echo 'Supported: Debian 13 or Ubuntu 24.04.' >&2; exit 2 ;;
    esac
    [ -d /run/systemd/system ] || { echo 'A running systemd is required.' >&2; exit 2; }
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3
fi
exec python3 "$SCRIPT_DIR/setup.py" "$@"
