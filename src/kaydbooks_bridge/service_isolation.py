"""Explicit root-operated migration of an existing Hermes gateway to scoped MCP.

Stops on error without restoring broad secret access. Rerunning the same command
is safe; saved profile/configuration backups support operator investigation.
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from .installer import InstallError, configure_hermes_remote, replace_private, run


def service_state(unit, option="is-active"):
    result = subprocess.run(["systemctl", option, unit], capture_output=True, text=True)
    if result.returncode not in (0, 1, 3, 4):
        raise InstallError("cannot inspect service state")
    return result.stdout.strip()


def profile_path(value):
    path = Path(value)
    root = Path("/var/lib/hermes/.hermes")
    if not path.is_absolute() or not path.is_relative_to(root) or ".." in path.parts:
        raise InstallError("profile must be inside the Hermes home")
    if not re.fullmatch(r"[/a-zA-Z0-9_.-]+", str(path)):
        raise InstallError("profile path contains unsupported service characters")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError("profile path must not contain symlinks")
    if not path.is_dir():
        raise InstallError("existing Hermes profile directory required")
    return path


def override_text(profile):
    profile = profile.as_posix()
    return f"""[Service]
SupplementaryGroups=
EnvironmentFile=
UnsetEnvironment=KAYDBOOKS_CONFIG KAYDBOOKS_TOOL_SECRET_FILE KAYDBOOKS_TOOL_TOKEN_ENV PYTHONPATH
Environment=HOME=/var/lib/hermes
Environment=HERMES_HOME={profile}
Environment=PATH=/var/lib/hermes/.local/bin:/var/lib/hermes/.hermes/node/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory={profile}
ExecStart=
ExecStart=/var/lib/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run
InaccessiblePaths=/etc/kaydbooks /var/lib/kaydbooks /var/log/kaydbooks
"""


def remove_bridge_group(user):
    import grp
    import pwd

    account = pwd.getpwnam(user)
    group = grp.getgrnam("kaydbooks")
    if account.pw_gid == group.gr_gid:
        raise InstallError("service still has Bridge as its primary group")
    if user in group.gr_mem:
        run("gpasswd", "-d", user, "kaydbooks", capture=True)


def verify_user(user):
    for path in (
        "/etc/kaydbooks/credentials.json",
        "/etc/kaydbooks/bridge-config.json",
        "/var/lib/kaydbooks",
    ):
        if not Path(path).exists():
            raise InstallError("isolation evidence requires existing protected paths")
        run("sudo", "-u", user, "test", "!", "-r", path)
        run("sudo", "-u", user, "test", "!", "-w", path)
    print(f"PASS {user} cannot read/write Bridge credentials, configuration or state")


def migrate(profile, base_url):
    if os.geteuid() != 0:
        raise InstallError("service isolation migration requires root")
    profile = profile_path(profile)
    runtime = Path("/var/lib/hermes/.hermes/hermes-agent/venv/bin/hermes")
    if not runtime.is_file():
        raise InstallError("expected Hermes runtime missing")
    worker = "kaydbooks-hermes-worker.service"
    if service_state(worker) not in ("inactive", "failed", "unknown") or service_state(
        worker, "is-enabled"
    ) not in ("disabled", "masked", "not-found", ""):
        raise InstallError("legacy worker is active/enabled; migrate its workflow before isolation")
    gateway = "hermes-gateway.service"
    state = service_state(gateway)
    if state not in ("active", "inactive", "failed"):
        raise InstallError("gateway must have a stable active/inactive/failed state")
    journal = Path("/etc/kaydbooks/isolation-hermes.json")
    intent = {
        "profile": str(profile),
        "server_url": base_url,
        "restart": state == "active",
        "complete": False,
    }
    if journal.exists():
        previous = json.loads(journal.read_text())
        if not previous.get("complete"):
            if any(previous.get(key) != intent[key] for key in ("profile", "server_url")):
                raise InstallError(
                    "resume the unfinished migration with its original profile and server"
                )
            intent["restart"] = previous["restart"]
    # Authenticate and validate the existing profile before stopping the gateway.
    configure_hermes_remote(runtime, profile, base_url, dry_run=True)
    replace_private(journal, intent)
    run("systemctl", "stop", gateway)
    configure_hermes_remote(runtime, profile, base_url)
    replace_private(
        Path("/etc/systemd/system/hermes-gateway.service.d/zz-kaydbooks-isolation.conf"),
        override_text(profile),
    )
    # The old worker imports code from a Hermes-writable plugin directory and uses
    # local Bridge state. Keep it explicitly unavailable until its separate migration.
    replace_private(
        Path("/etc/systemd/system/kaydbooks-hermes-worker.service.d/zz-kaydbooks-isolation.conf"),
        "[Service]\nSupplementaryGroups=\nEnvironmentFile=\nExecStart=\nExecStart=/bin/false\nRestart=no\n"
        "InaccessiblePaths=/etc/kaydbooks /var/lib/kaydbooks /var/log/kaydbooks\n",
    )
    remove_bridge_group("hermes")
    run("systemctl", "daemon-reload")
    verify_user("hermes")
    run("sudo", "-u", "hermes", "test", "!", "-w", "/etc/kaydbooks-hermes")
    run(
        "sudo",
        "-u",
        "hermes",
        "/opt/kaydbooks/current/bin/kaydbooks-bridge-remote-client",
        "--config",
        "/etc/kaydbooks-hermes/client.json",
        "--check",
        cwd="/var/lib/hermes",
    )
    # Restore only a previously running service; never enroll or enable a channel.
    if intent["restart"]:
        run("systemctl", "start", gateway)
        run("systemctl", "is-active", gateway)
    replace_private(journal, {**intent, "complete": True})
    print("PASS Hermes service isolation applied; verify WhatsApp and company catalog separately")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--server-url", required=True)
    args = parser.parse_args()
    try:
        migrate(args.profile, args.server_url)
    except (InstallError, OSError, subprocess.CalledProcessError):
        raise SystemExit(
            "Isolation incomplete; inspect the last check and rerun after correction. "
            "Do not restore broad service-group access."
        ) from None


if __name__ == "__main__":
    main()
