"""Generate private Hermes connection files without modifying a running deployment."""

import re
import secrets

from .config import BridgeError, Config, strict_keys
from .onboarding import _write, private_path, read_json, restrict_directory

TOOLS = [
    "company_catalog_v1",
    "capture_document_v1",
    "extract_document_v1",
    "table_intake_v1",
    "qbwc_entry_v1",
    "batch_preview_v1",
    "batch_status_v1",
]


def _ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _ssh_path(path):
    # OpenSSH joins remote arguments into a command line. Preserve Windows spaces
    # and reject command-shell metacharacters before constructing that line.
    if any(c in str(path) for c in '"%`$&|<>^\r\n\0'):
        raise BridgeError("launcher path contains unsupported remote-shell characters")
    return '"' + str(path) + '"'


def generate(request_path, destination):
    request = read_json(request_path)
    strict_keys(
        request,
        {
            "config",
            "credentials",
            "python",
            "company",
            "operator",
            "reviewer",
            "ssh_host",
            "chat_id",
            "sender_ids",
        },
    )
    config_path = private_path(request["config"])
    config = Config.load(config_path)
    credentials = read_json(request["credentials"])
    if not isinstance(credentials, dict):
        raise BridgeError("credential object required")
    python = private_path(request["python"])
    root = private_path(destination)
    if not python.is_file() or python.name.lower() != "python.exe":
        raise BridgeError("installed Windows runtime python.exe required")
    if root.exists() or not root.parent.is_dir():
        raise BridgeError("new destination under an existing private parent required")
    _ssh_path(root / "start-tools.ps1")
    company, operator, reviewer = (request[k] for k in ("company", "operator", "reviewer"))
    if any(not isinstance(v, str) for v in (company, operator, reviewer)):
        raise BridgeError("explicit company and principal names required")
    if company not in config.companies or operator == reviewer:
        raise BridgeError("configured company and separate reviewer required")
    policy = config.companies[company]
    if not policy.approval_required or policy.allow_self_approval:
        raise BridgeError("independent company approval must be enabled")
    for permission in ("read", "prepare", "validate", "submit", "post-sample"):
        config.authorize(operator, company, permission)
    config.authorize(reviewer, company, "approve")
    names = [config.principals[p]["token_env"] for p in (operator, reviewer)]
    values = [credentials.get(n) for n in names]
    if (
        len(set(names)) != 2
        or any(
            not isinstance(v, str) or len(v) < 32 or any(c in v for c in "\r\n\0") for v in values
        )
        or len(set(values)) != 2
    ):
        raise BridgeError("distinct operator and reviewer credentials required")
    # Authentication iterates configured principals; a reused credential must not
    # resolve to a different principal than the one selected for this launcher.
    for name, value in zip(names, values, strict=True):
        if any(
            credentials.get(p["token_env"]) == value and p["token_env"] != name
            for p in config.principals.values()
        ):
            raise BridgeError("principal credential reused")
    host = request["ssh_host"]
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host):
        raise BridgeError("configured SSH host alias required")
    chat, senders = request["chat_id"], request["sender_ids"]
    if (
        not isinstance(chat, str)
        or not re.fullmatch(r"[0-9]+@(lid|s\.whatsapp\.net)", chat)
        or not isinstance(senders, list)
        or not senders
        or any(s != chat for s in senders)
        or len(set(senders)) != len(senders)
    ):
        raise BridgeError("one native operator direct-chat identity required")
    root.mkdir(mode=0o700)
    restrict_directory(root)
    signing = secrets.token_urlsafe(48)
    _write(root / "tools-credentials.json", {names[0]: values[0]})
    _write(root / "channel-credentials.json", dict(zip(names, values, strict=True)))
    _write(root / "channel-secrets.json", {"KAYDBOOKS_CHANNEL_SIGNING_SECRET": signing})
    common = {"company": company, "chat_id": chat, "sender_ids": senders, "allow_outbound": False}
    _write(
        root / "windows-channel.json",
        {
            **common,
            "operator_token_env": names[0],
            "reviewer_token_env": names[1],
            "signing_secret_env": "KAYDBOOKS_CHANNEL_SIGNING_SECRET",
            "channel_secret_file": str(root / "channel-secrets.json"),
            "outbound_batch_ids": [],
        },
    )
    base = (
        "$ErrorActionPreference='Stop'\n"
        "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        "$env:PYTHONIOENCODING='utf-8'\n"
        f"$env:KAYDBOOKS_CONFIG={_ps(config_path)}\n"
    )
    tools = base + (
        "$env:KAYDBOOKS_TOOL_SECRET_FILE=Join-Path $PSScriptRoot 'tools-credentials.json'\n"
        f"$env:KAYDBOOKS_TOOL_TOKEN_ENV={_ps(names[0])}\n"
        f"& {_ps(python)} -m kaydbooks_bridge.hermes_tools\nexit $LASTEXITCODE\n"
    )
    channel = (
        "param([switch]$Clock)\n"
        + base
        + (
            "$env:KAYDBOOKS_TOOL_SECRET_FILE=Join-Path $PSScriptRoot 'channel-credentials.json'\n"
            "$env:KAYDBOOKS_CHANNEL_CONFIG=Join-Path $PSScriptRoot 'windows-channel.json'\n"
            f"if ($Clock) {{ & {_ps(python)} -m kaydbooks_bridge.hermes_channel --clock }}\n"
            f"else {{ & {_ps(python)} -m kaydbooks_bridge.hermes_channel }}\nexit $LASTEXITCODE\n"
        )
    )
    (root / "start-tools.ps1").write_text(tools, encoding="utf-8-sig")
    (root / "start-channel.ps1").write_text(channel, encoding="utf-8-sig")
    ssh = [
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        host,
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-File",
    ]
    _write(
        root / "linux-channel.json",
        {
            **common,
            "signing_secret": signing,
            "ssh_command": ["ssh", *ssh, _ssh_path(root / "start-channel.ps1")],
        },
    )
    _write(
        root / "hermes-mcp-fragment.json",
        {
            "mcp_servers": {
                "kaydbooks": {
                    "command": "ssh",
                    "args": [*ssh, _ssh_path(root / "start-tools.ps1")],
                    "enabled": True,
                    "timeout": 60,
                    "connect_timeout": 30,
                    "supports_parallel_tool_calls": False,
                    "tools": {"include": TOOLS, "resources": False, "prompts": False},
                }
            },
        },
    )
    (root / "INSTALL.txt").write_text(
        "Keep this directory private on Windows. It references the existing Bridge config/state.\n"
        "Copy only linux-channel.json to the trusted Linux channel service (chmod 600).\n"
        "Set KAYDBOOKS_HERMES_CONFIG to its Linux path for both gateway and worker.\n"
        "Merge hermes-mcp-fragment.json into the actual operator profile; preserve other settings.\n"
        "Install the KaydBooks plugin in that profile and add kaydbooks to plugins.enabled.\n"
        "Verify host keys, SSH clock RPC and MCP discovery before enabling the worker.\n"
        "Outgoing messages are disabled on both hosts. Enable only for approved chats/batches.\n"
        "No service, QWC registration, company grant or posting state was changed.\n"
        "Use separate profiles/workers for separate company/chat bindings. A single profile's\n"
        "confirmation hook uses one configured company; do not overwrite it for another file.\n",
        encoding="utf-8",
    )
    return {
        "status": "generated-disabled",
        "company": company,
        "files": 9,
        "outbound_enabled": False,
        "posting_changed": False,
        "next": "install in actual operator profile and verify SSH/MCP before enabling delivery",
    }
