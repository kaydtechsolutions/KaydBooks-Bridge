"""First-install wizard. Standard library only until the KB wheel is installed.

No company writes, account enrollment, existing-deployment adoption, or implicit upgrade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = "https://github.com/kaydtechsolutions/KaydBooks-Bridge.git"
COMPONENTS = {"core", "chatgpt", "claude", "gemini", "hermes", "composio"}
PACKAGES = [
    "git",
    "curl",
    "ca-certificates",
    "python3",
    "python3-venv",
    "sqlite3",
    "caddy",
    "xz-utils",
    "sudo",
]
READ_TOOLS = ["company_catalog_v1", "entry_status_v1", "entry_preview_v1", "batch_status_v1"]


class InstallError(ValueError):
    pass


def run(*args, capture=False, **kwargs):
    return subprocess.run(
        [str(a) for a in args],
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        **kwargs,
    ).stdout


def private_write(path, value):
    """Exclusive, restrictive creation; never follow or replace an existing file."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError("private path contains a symbolic link")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = value if isinstance(value, str) else json.dumps(value, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def options(company, currency, edition, components):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", company):
        raise InstallError("company must be a lowercase ID, maximum 40 characters")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise InstallError("currency must be three uppercase letters")
    if edition not in (1, 2, 4, 8, 15):
        raise InstallError("edition flags must be 1, 2, 4, 8, or 15")
    selected = set(components.split(",")) | {"core"}
    if not selected <= COMPONENTS:
        raise InstallError("unknown component; use core,chatgpt,claude,gemini,hermes,composio")
    return {
        "company": company,
        "currency": currency,
        "edition": edition,
        "components": sorted(selected),
    }


def valid_hostname(host):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.[a-z0-9-]+\.ts\.net", host):
        raise InstallError("expected the server's full Tailscale MagicDNS name")
    return host


def preflight(source):
    checks = []
    release = {}
    if Path("/etc/os-release").is_file():
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip('"')
    checks.append(
        (
            "Debian 13 / Ubuntu 24.04",
            (release.get("ID"), release.get("VERSION_ID"))
            in {("debian", "13"), ("ubuntu", "24.04")},
        )
    )
    checks.append(("systemd", Path("/run/systemd/system").is_dir()))
    checks.append(("2+ CPU cores", (os.cpu_count() or 0) >= 2))
    mem = Path("/proc/meminfo")
    memory = int(re.search(r"MemTotal:\s+(\d+)", mem.read_text())[1]) if mem.exists() else 0
    limit = Path("/sys/fs/cgroup/memory.max")
    if limit.exists() and limit.read_text().strip().isdigit():
        memory = min(memory, int(limit.read_text().strip()) // 1024)
    # Account for memory reserved by the kernel on a nominal 4 GiB machine.
    checks.append(("4 GiB RAM", memory >= 3_500_000))
    checks.append(
        (
            "10 GiB free disk (20 GiB+ volume recommended)",
            shutil.disk_usage(source).free >= 10 * 1024**3,
        )
    )
    import stat

    tun = Path("/dev/net/tun")
    checks.append(
        (
            "TUN device (enable on LXC host if missing)",
            tun.exists() and stat.S_ISCHR(tun.stat().st_mode),
        )
    )
    for label, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {label}")
    missing = []
    if shutil.which("dpkg-query"):
        for package in PACKAGES:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True
            )
            if result.returncode or result.stdout.strip() != "install ok installed":
                missing.append(package)
    else:
        missing = PACKAGES[:]
    print("MISSING packages (installed automatically): " + (", ".join(missing) or "none"))
    print(
        "Tailscale: "
        + (
            "installed"
            if shutil.which("tailscale")
            else "will install from signed package repository"
        )
    )
    return all(ok for _, ok in checks), missing


def source_revision(source):
    origin = run("git", "-C", source, "remote", "get-url", "origin", capture=True).strip()
    if origin.rstrip("/") != REPOSITORY:
        raise InstallError("checkout must use the official KaydBooks GitHub origin")
    if run("git", "-C", source, "status", "--porcelain", capture=True).strip():
        raise InstallError("use a clean checkout; preserve local edits separately")
    return run("git", "-C", source, "rev-parse", "HEAD", capture=True).strip()


def claim_install(etc, settings, revision):
    """Only resume this install; do not adopt or overwrite an older deployment."""
    etc = Path(etc)
    if any(p.is_symlink() for p in (etc, *etc.parents)):
        raise InstallError("configuration path must not contain symlinks")
    marker = etc / "installer.json"
    expected = {"schema_version": 1, "settings": settings, "source_commit": revision}
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != expected:
            raise InstallError("existing installation differs; use the upgrade runbook")
    else:
        if etc.exists() and any(etc.iterdir()):
            raise InstallError(
                "existing private configuration detected; fresh installer will not overwrite it"
            )
        private_write(marker, expected)


def provision(etc, state, settings, host):
    """Write a new bundle in staging, then atomically rename. Re-runs retain secrets/IDs."""
    etc, state = Path(etc), Path(state)
    host = valid_hostname(host)
    destination = etc / "initial"
    company = settings["company"]
    base = f"https://{host}"
    if destination.exists():
        if destination.is_symlink():
            raise InstallError("setup bundle must not be a symlink")
        profile = json.loads((destination / "profile.json").read_text())
        if profile["endpoint_url"] != f"{base}/qbwc/{company}":
            raise InstallError(
                "hostname changed; review existing QWC registration before migrating"
            )
        return destination
    principals = {
        "operator": {"token_env": "KAYDBOOKS_OPERATOR_SECRET", "companies": {company: ["read"]}}
    }
    policy = {"schema_version": 1, "principals": {}}
    credentials = {
        "KAYDBOOKS_CONNECTOR_SECRET": secrets.token_urlsafe(48),
        "KAYDBOOKS_OPERATOR_SECRET": secrets.token_urlsafe(48),
    }
    for component in sorted(
        set(settings["components"]) & {"chatgpt", "claude", "gemini", "hermes"}
    ):
        source = "whatsapp" if component == "hermes" else component
        key = f"KAYDBOOKS_{component.upper()}_SECRET"
        principals[source] = {"token_env": key, "companies": {company: ["read"]}}
        credentials[key] = secrets.token_urlsafe(48)
        policy["principals"][source] = {
            "source": source,
            "companies": [company],
            "tools": READ_TOOLS[:],
        }
    # Read-only baseline. Optional services cannot grant posting authority.
    config = {
        "schema_version": 1,
        "mode": "simulation",
        "state_root": str(state),
        "companies": {
            company: {
                "simulation_identity": company,
                "currency": settings["currency"],
                "max_total": "100.00",
                "customers": ["configure-customer"],
                "items": ["configure-item"],
                "sources": ["documents"],
                "approval_required": True,
            }
        },
        "principals": principals,
        "connectors": {
            f"quickbooks-{company}": {
                "company": company,
                "password_env": "KAYDBOOKS_CONNECTOR_SECRET",
                "identity_fields": ["CompanyName", "LegalCompanyName", "EIN"],
                "identity_sha256": "0" * 64,
            }
        },
    }
    profile = {
        "schema_version": 2,
        "access_mode": "bridge-gated",
        "app_name": f"KaydBooks - {company}",
        "endpoint_url": f"{base}/qbwc/{company}",
        "support_url": f"{base}/support",
        "username": f"quickbooks-{company}",
        "owner_id": "{" + str(uuid.uuid4()).upper() + "}",
        "file_id": "{" + str(uuid.uuid4()).upper() + "}",
        "run_every_seconds": 300,
        "auth_flags": settings["edition"],
        "is_read_only": False,
        "unattended_mode_pref": "umpOptional",
        "app_unique_name": f"KB Bridge {company}",
    }
    with tempfile.TemporaryDirectory(prefix=".setup-", dir=etc) as folder:
        staging = Path(folder) / "bundle"
        staging.mkdir(mode=0o700)
        for name, value in {
            "bridge-config.json": config,
            "credentials.json": credentials,
            "remote-policy.json": policy,
            "composio-policy.json": {"schema_version": 1, "companies": {}},
            "profile.json": profile,
        }.items():
            private_write(staging / name, value)
        staging.rename(destination)
    return destination


def client_config(component, host):
    entry = {"headers": {"Authorization": f"Bearer ${{KAYDBOOKS_{component.upper()}_SECRET}}"}}
    if component == "gemini":
        entry.update(httpUrl=f"https://{host}/mcp", trust=False, includeTools=READ_TOOLS[:])
    else:
        entry.update(type="http", url=f"https://{host}/mcp")
    return {"mcpServers": {"kaydbooks": entry}}


def publish_config(etc, bundle, host):
    etc = Path(etc)
    # Only overwrite installer-supplied placeholder policies during the first publication.
    if not (etc / "bridge-config.json").exists():
        for name in ("credentials.json", "remote-policy.json", "composio-policy.json"):
            target = etc / name
            if target.is_symlink():
                raise InstallError("unexpected symlink")
            run("install", "-o", "root", "-g", "kaydbooks", "-m", "0640", bundle / name, target)
        # Publish the config last as the completion marker for this step.
        run(
            "install",
            "-o",
            "root",
            "-g",
            "kaydbooks",
            "-m",
            "0640",
            bundle / "bridge-config.json",
            etc / "bridge-config.json",
        )
    env = etc / "bridge.env"
    text = env.read_text()
    if "REPLACE_TAILNET" in text:
        text = text.replace("kaydbooks-server.REPLACE_TAILNET.ts.net", host)
        env.write_text(text, encoding="utf-8")
    if f"KAYDBOOKS_BASE_URL=https://{host}\n" not in text:
        raise InstallError("existing bridge.env hostname differs")


def request(url, body=None, token=None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    if data:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers=headers), timeout=15
        ) as response:
            return response.status, response.read(1_048_576)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4096)


def verify(base, etc):
    """Tests the running HTTP boundary with real tokens but prints no credentials/payloads."""
    failed = False

    def check(label, success):
        nonlocal failed
        print(f"{'PASS' if success else 'FAIL'} {label}")
        failed |= not success

    for path in ("/healthz", "/health"):
        for _ in range(15):
            try:
                code, payload = request(base + path)
                if code == 200:
                    break
            except (OSError, urllib.error.URLError):
                code = 0
            time.sleep(1)
        check(path, code == 200)
        if code == 200:
            health = json.loads(payload)
            check(f"{path}: ready", health.get("status") == "ready")
            if path == "/healthz":
                check("production posting disabled", health.get("live_posting") is False)
    check("unknown route denied", request(base + "/not-a-kb-route")[0] == 404)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "kb-installer", "version": "1"},
        },
    }
    check("MCP without token denied", request(base + "/mcp", initialize)[0] == 401)
    config = json.loads((etc / "bridge-config.json").read_text())
    credentials = json.loads((etc / "credentials.json").read_text())
    policy = json.loads((etc / "remote-policy.json").read_text())
    for actor, rule in policy["principals"].items():
        token = credentials[config["principals"][actor]["token_env"]]
        code, payload = request(base + "/mcp", initialize, token)
        check(f"{actor}: MCP initialize", code == 200 and "result" in json.loads(payload))
        rpc = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        code, payload = request(base + "/mcp", rpc, token)
        from .remote_mcp import TOOL_NAMES

        # Discovery lists the frozen server contract; per-source policy governs execution.
        names = {tool["name"] for tool in json.loads(payload).get("result", {}).get("tools", [])}
        check(f"{actor}: MCP tool contract", code == 200 and names == TOOL_NAMES)
        for company, permitted in ((rule["companies"][0], True), ("installer-unassigned", False)):
            rpc = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "company_catalog_v1",
                    "arguments": {"company": company},
                },
            }
            code, payload = request(base + "/mcp", rpc, token)
            error = json.loads(payload).get("result", {}).get("isError")
            check(
                f"{actor}: {'assigned read' if permitted else 'wrong-company denial'}",
                code == 200 and error is (not permitted),
            )
        rpc["params"] = {
            "name": "entry_review_v1",
            "arguments": {
                "company": rule["companies"][0],
                "action": "dispatch",
                "job_id": "installer-probe-no-job",
            },
        }
        code, payload = request(base + "/mcp", rpc, token)
        check(
            f"{actor}: dispatch denied",
            code == 200 and json.loads(payload).get("result", {}).get("isError") is True,
        )
    if not policy["principals"]:
        print("SKIP authenticated AI checks: core-only installation has no remote principals")
    return not failed


def stage_optional(etc, settings, host):
    notes = []
    for component in settings["components"]:
        path = etc / f"{component}-client.json"
        if component in {"claude", "gemini"}:
            if not path.exists():
                private_write(path, client_config(component, host))
            notes.append(
                f"{component}: import {path} on a Tailscale client; set its token privately, then test /mcp."
            )
        elif component == "chatgpt":
            notes.append(
                "chatgpt: dedicated token created; finish OpenAI account/tunnel enrollment in AUTO_INSTALL.md."
            )
        elif component == "hermes":
            notes.append(
                "hermes: runtime and read-only MCP configured; finish model/WhatsApp setup in AUTO_INSTALL.md."
            )
        elif component == "composio":
            notes.append(
                "composio: disabled policy created; connect your accounts and review exact tool/version grants in AUTO_INSTALL.md."
            )
    for note in notes:
        print("ACTION " + note)
    return notes


def install_hermes():
    runtime = Path("/var/lib/hermes/.hermes/hermes-agent/venv/bin/hermes")
    if not runtime.exists():
        with tempfile.TemporaryDirectory(prefix="kb-hermes-") as directory:
            script = Path(directory) / "install.sh"
            run(
                "curl",
                "--fail",
                "--location",
                "--proto",
                "=https",
                "--tlsv1.2",
                "https://hermes-agent.nousresearch.com/install.sh",
                "-o",
                script,
            )
            # Service account, never root; no shell interpolation of external inputs.
            os.chmod(directory, 0o755)
            os.chmod(script, 0o644)
            run(
                "sudo",
                "-u",
                "hermes",
                "-H",
                "bash",
                script,
                "--skip-browser",
                "--skip-computer-use",
                # sudo -H changes HOME but retains cwd. uv searches cwd/parents
                # for environments, and the service user cannot inspect /root.
                cwd="/var/lib/hermes",
            )
    if not runtime.exists():
        raise InstallError(
            "Hermes runtime location differs; inspect installer output before configuring units"
        )
    etc = Path("/etc/kaydbooks")
    credentials = json.loads((etc / "credentials.json").read_text())
    tool_file = etc / "hermes-tools.json"
    if not tool_file.exists():
        private_write(
            tool_file, {"KAYDBOOKS_HERMES_SECRET": credentials["KAYDBOOKS_HERMES_SECRET"]}
        )
    run("chown", "root:hermes", tool_file)
    run("chmod", "0640", tool_file)
    # Use the actual installer's default profile; no deployment-specific user name.
    hermes_home = Path("/var/lib/hermes/.hermes")
    fragment = {
        "mcp_servers": {
            "kaydbooks": {
                "command": "/opt/kaydbooks/current/bin/kaydbooks-bridge-tools",
                "args": [],
                "enabled": True,
                "supports_parallel_tool_calls": False,
                "env": {
                    "KAYDBOOKS_CONFIG": "/etc/kaydbooks/bridge-config.json",
                    "KAYDBOOKS_TOOL_SECRET_FILE": str(tool_file),
                    "KAYDBOOKS_TOOL_TOKEN_ENV": "KAYDBOOKS_HERMES_SECRET",
                },
                "tools": {
                    "include": ["company_catalog_v1", "batch_status_v1"],
                    "resources": False,
                    "prompts": False,
                },
            }
        },
    }
    fragment_path = etc / "hermes-fragment.json"
    if not fragment_path.exists():
        private_write(fragment_path, fragment)
    # Hermes supplies PyYAML. Preserve the installer-generated provider settings.
    run(
        runtime.parent / "python",
        "-c",
        """
import json, pathlib, yaml
path = pathlib.Path('/var/lib/hermes/.hermes/config.yaml')
data = yaml.safe_load(path.read_text()) if path.exists() else {}
data = data or {}
fragment = json.load(open('/etc/kaydbooks/hermes-fragment.json'))
servers = data.setdefault('mcp_servers', {})
if 'kaydbooks' not in servers:
    servers.update(fragment['mcp_servers'])
    path.write_text(yaml.safe_dump(data, sort_keys=False))
""",
    )
    run("chown", "hermes:hermes", hermes_home / "config.yaml")
    override = Path("/etc/systemd/system/hermes-gateway.service.d/installer.conf")
    if not override.exists():
        private_write(
            override,
            f"""[Service]
Environment=HERMES_HOME={hermes_home}
Environment=PATH=/var/lib/hermes/.local/bin:{hermes_home}/node/bin:/usr/local/bin:/usr/bin:/bin
Environment=KAYDBOOKS_TOOL_SECRET_FILE={tool_file}
Environment=KAYDBOOKS_TOOL_TOKEN_ENV=KAYDBOOKS_HERMES_SECRET
WorkingDirectory={hermes_home}
ExecStart=
ExecStart={runtime.parent}/python -m hermes_cli.main gateway run
""",
        )
    run("systemctl", "daemon-reload")
    print(
        "PASS Hermes runtime/MCP configured; ACTION model login and WhatsApp pairing required before enabling gateway"
    )


def build_wheel(source, revision):
    cache = Path("/var/cache/kaydbooks") / revision
    if any(p.is_symlink() for p in (cache, *cache.parents)):
        raise InstallError("build cache must not be a symlink")
    receipt = cache / "sha256.json"
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved["filename"] != "kaydbooks_bridge-0.2.0-py3-none-any.whl":
            raise InstallError("invalid cached wheel name")
        wheel = cache / saved["filename"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != saved["sha256"]:
            raise InstallError("cached wheel checksum mismatch")
        return wheel, saved["sha256"]
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="kb-build-") as directory:
        build = Path(directory)
        run("python3", "-m", "venv", build / "venv")
        python = build / "venv/bin/python"
        run(python, "-m", "pip", "install", "build")
        run(python, "-m", "build", "--wheel", "--outdir", build / "dist", source)
        wheels = list((build / "dist").glob("kaydbooks_bridge-0.2.0-*.whl"))
        if len(wheels) != 1 or wheels[0].name != "kaydbooks_bridge-0.2.0-py3-none-any.whl":
            raise InstallError("expected one v0.2.0 wheel")
        digest = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
        wheel = cache / wheels[0].name
        shutil.copyfile(wheels[0], wheel)
        private_write(receipt, {"filename": wheel.name, "sha256": digest})
        return wheel, digest


def apply_install(source, settings, revision, missing, install_hermes_runtime=False):
    etc = Path("/etc/kaydbooks")
    if not (etc / "installer.json").exists():
        for path in ("/opt/kaydbooks/current", "/etc/caddy/Caddyfile"):
            if Path(path).exists():
                raise InstallError(
                    f"existing deployment/service at {path}; use a fresh host or the upgrade runbook"
                )
    claim_install(etc, settings, revision)
    # Prevent a package post-install hook from exposing Caddy's default listener.
    run("systemctl", "mask", "--now", "caddy.service")
    if missing:
        run("apt-get", "update")
        run(
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            *missing,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
    if not shutil.which("tailscale"):
        release = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
        distro = release["ID"].strip('"')
        codename = "trixie" if distro == "debian" else "noble"
        repo = f"https://pkgs.tailscale.com/stable/{distro}/{codename}"
        run(
            "curl",
            "--fail",
            "--proto",
            "=https",
            repo + ".noarmor.gpg",
            "-o",
            "/usr/share/keyrings/tailscale-archive-keyring.gpg",
        )
        run(
            "curl",
            "--fail",
            "--proto",
            "=https",
            repo + ".tailscale-keyring.list",
            "-o",
            "/etc/apt/sources.list.d/tailscale.list",
        )
        run("apt-get", "update")
        run("apt-get", "install", "-y", "tailscale")
    run("systemctl", "enable", "--now", "tailscaled")
    # Tailscale returns exit 1 along with valid JSON before the first login.
    result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise InstallError("Tailscale status failed")
    status = json.loads(result.stdout)
    if status.get("BackendState") != "Running":
        print("ACTION Complete the Tailscale login using the displayed URL.")
        run("tailscale", "up", "--timeout=120s")
        status = json.loads(run("tailscale", "status", "--json", capture=True))
    host = valid_hostname(status.get("Self", {}).get("DNSName", "").rstrip("."))
    state = Path("/var/lib/kaydbooks/state")
    # The legacy checksum installer preserves an existing environment/configuration.
    wheel, digest = build_wheel(source, revision)
    run("sh", source / "deploy/install.sh", wheel, digest)
    bundle = provision(etc, state, settings, host)
    publish_config(etc, bundle, host)
    run("install", "-d", "-o", "kaydbooks", "-g", "kaydbooks", "-m", "2770", state)
    python = Path("/opt/kaydbooks/current/bin/python")
    run("sudo", "-u", "kaydbooks", python, "-m", "kaydbooks_bridge.installer", "--initialize-state")
    export = etc / "export"
    export.mkdir(mode=0o700, exist_ok=True)
    run(
        "/opt/kaydbooks/current/bin/kaydbooks-bridge-qbwc-config",
        "generate-qwc",
        env={
            **os.environ,
            "KAYDBOOKS_QBWC_PROFILE": str(bundle / "profile.json"),
            "KAYDBOOKS_QBWC_QWC_FILE": str(export / f"KaydBooks-{settings['company']}.qwc"),
        },
    )
    run("caddy", "validate", "--config", "/etc/caddy/Caddyfile")
    run("systemctl", "unmask", "caddy.service")
    run("systemctl", "enable", "--now", "caddy", "kaydbooks-bridge", "kaydbooks-remote-mcp")
    run("tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8088", timeout=120)
    run(python, "-m", "kaydbooks_bridge.installer", "--verify")
    if "hermes" in settings["components"] or install_hermes_runtime:
        install_hermes()
    stage_optional(etc, settings, host)
    print(f"PASS Linux installation: https://{host}/app")
    print(
        f"ACTION Windows: copy {export}/KaydBooks-{settings['company']}.qwc, authorize the sample company, then bind its identity."
    )
    print(
        "ACTION Credentials stay in /etc/kaydbooks/credentials.json. Read KAYDBOOKS_CONNECTOR_SECRET locally for QBWC."
    )
    print("Accounting qualification: PENDING. Production posting remains disabled.")


def main(argv=None, *, source=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only prerequisite check")
    parser.add_argument(
        "--yes", action="store_true", help="use supplied/default choices without wizard"
    )
    parser.add_argument("--company", default="company-a")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--edition", type=int, default=8)
    parser.add_argument("--components", default="core")
    parser.add_argument(
        "--install-hermes",
        action="store_true",
        help="also run official Hermes runtime installer as hermes user",
    )
    parser.add_argument("--initialize-state", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check installed HTTPS, authentication and company isolation",
    )
    args = parser.parse_args(argv)
    try:
        if args.initialize_state:
            from .config import Config
            from .store import Store

            config = Config.load("/etc/kaydbooks/bridge-config.json")
            for company in config.companies:
                Store(config.root, company)
            return 0
        if args.verify:
            etc = Path("/etc/kaydbooks")
            env = dict(
                line.split("=", 1)
                for line in (etc / "bridge.env").read_text().splitlines()
                if line.startswith("KAYDBOOKS_")
            )
            return 0 if verify(env["KAYDBOOKS_BASE_URL"], etc) else 1
        source = Path(source or Path(__file__).resolve().parents[2])
        ok, missing = preflight(source)
        if args.check:
            return 0 if ok and not missing and shutil.which("tailscale") else 1
        if not ok:
            raise InstallError("fix failed host prerequisites before installing")
        if os.geteuid() != 0:
            raise InstallError("run with sudo")
        if not args.yes:
            args.company = input(f"Company ID [{args.company}]: ").strip() or args.company
            args.currency = input(f"Currency [{args.currency}]: ").strip() or args.currency
            args.components = (
                input("Components: core,chatgpt,claude,gemini,hermes,composio [core]: ").strip()
                or args.components
            )
        if args.install_hermes:
            args.components += ",hermes"
        settings = options(args.company, args.currency, args.edition, args.components)
        revision = source_revision(source)
        print(f"Source GitHub commit: {revision}")
        apply_install(source, settings, revision, missing, args.install_hermes)
        return 0
    except (InstallError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # External command output is already visible. Never print a token-bearing argv.
        print(
            f"FAIL {exc if isinstance(exc, InstallError) else type(exc).__name__}; correct the issue and rerun with the same choices.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
