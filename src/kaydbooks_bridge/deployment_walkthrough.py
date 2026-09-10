"""Offline, secret-safe verification of one isolated company deployment."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import BridgeError, Config, strict_keys
from .hermes_setup import TOOLS
from .onboarding import private_path, read_json, target_values
from .validation import digest

WORKFLOW_PERMISSIONS = {"read", "prepare", "validate", "submit", "post-sample"}
ENTRY_GATES = (
    "sample_sales_receipt_posting",
    "sample_posting",
    "sample_credit_posting",
    "sample_payment_posting",
    "sample_bill_posting",
    "sample_journal_posting",
    "sample_inventory_transfer_posting",
    "sample_check_posting",
)
MAPPING_GROUPS = (
    "account_roles",
    "invoice_masters",
    "bill_masters",
    "payment_masters",
    "inventory_transfer_masters",
    "journal_masters",
)


def _bounded_xml(path: str | Path) -> ET.Element:
    source = private_path(path)
    if not source.is_file() or source.stat().st_size > 131072:
        raise BridgeError("bounded private QWC file required")
    try:
        root = ET.fromstring(source.read_text(encoding="utf-8-sig"))
    except ET.ParseError as exc:
        raise BridgeError("invalid QWC file") from exc
    if root.tag != "QBWCXML":
        raise BridgeError("invalid QWC file")
    return root


def _valid_secret(credentials: dict, name: str) -> bool:
    value = credentials.get(name)
    return (
        isinstance(value, str)
        and len(value) >= 32
        and not any(character in value for character in "\r\n\0")
    )


def _audit_valid(db: sqlite3.Connection, company: str) -> bool:
    previous = "0" * 64
    for row in db.execute("SELECT * FROM audit ORDER BY sequence"):
        value = {
            "company": company,
            "at": row["at"],
            "actor": row["actor"],
            "job_id": row["job_id"],
            "event": row["event"],
            "data": json.loads(row["data"]),
            "previous_hash": previous,
        }
        if row["previous_hash"] != previous or row["hash"] != digest(value):
            return False
        previous = row["hash"]
    return True


def _state_checks(config: Config, company: str, connector_id: str) -> dict[str, bool]:
    database = config.root / company / "jobs.sqlite3"
    if not database.is_file() or database.is_symlink():
        return {
            "state_initialized": False,
            "state_company_bound": False,
            "audit_valid": False,
            "posting_paused": False,
            "qbwc_connection_observed": False,
        }
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchone() is None
        paused = bool(db.execute("SELECT paused FROM control").fetchone()[0])
        observed = db.execute(
            "SELECT 1 FROM qbwc_sessions WHERE connector=? AND state='closed' LIMIT 1",
            (connector_id,),
        ).fetchone()
        return {
            "state_initialized": integrity and foreign_keys,
            "state_company_bound": metadata == {"schema_version": "1", "company": company},
            "audit_valid": _audit_valid(db, company),
            "posting_paused": paused,
            "qbwc_connection_observed": observed is not None,
        }


def _hermes_checks(root: str | Path, config: Config, company: str, operator: str, reviewer: str):
    folder = private_path(root)
    required = {
        "tools-credentials.json",
        "channel-credentials.json",
        "channel-secrets.json",
        "windows-channel.json",
        "linux-channel.json",
        "hermes-mcp-fragment.json",
        "start-tools.ps1",
        "start-channel.ps1",
        "INSTALL.txt",
    }
    files_present = folder.is_dir() and required <= {
        p.name for p in folder.iterdir() if p.is_file()
    }
    if not files_present:
        return {
            "hermes_bundle_complete": False,
            "hermes_company_bound": False,
            "hermes_roles_separated": False,
            "hermes_tool_allowlist_exact": False,
            "hermes_serial_calls": False,
        }
    windows = read_json(folder / "windows-channel.json")
    tools_credentials = read_json(folder / "tools-credentials.json")
    channel_credentials = read_json(folder / "channel-credentials.json")
    fragment = read_json(folder / "hermes-mcp-fragment.json")
    server = fragment.get("mcp_servers", {}).get("kaydbooks", {})
    tool_env = config.principals[operator]["token_env"]
    reviewer_env = config.principals[reviewer]["token_env"]
    return {
        "hermes_bundle_complete": True,
        "hermes_company_bound": windows.get("company") == company,
        "hermes_roles_separated": (
            operator != reviewer
            and windows.get("operator_token_env") == tool_env
            and windows.get("reviewer_token_env") == reviewer_env
            and set(tools_credentials) == {tool_env}
            and set(channel_credentials) == {tool_env, reviewer_env}
        ),
        "hermes_tool_allowlist_exact": server.get("tools", {}).get("include") == TOOLS,
        "hermes_serial_calls": server.get("supports_parallel_tool_calls") is False,
    }


def inspect_deployment(request_path: str | Path) -> dict:
    """Verify a configured deployment without printing identifiers, paths, or secrets."""
    request = read_json(request_path)
    strict_keys(
        request,
        {
            "config",
            "target",
            "credentials",
            "qwc",
            "hermes_bundle",
            "company",
            "connector",
            "operator",
            "reviewer",
        },
    )
    config = Config.load(private_path(request["config"]))
    target = target_values(read_json(request["target"]))
    company = request["company"]
    connector_id = request["connector"]
    operator = request["operator"]
    reviewer = request["reviewer"]
    if target["company_id"] != company or company not in config.companies:
        raise BridgeError("target and explicit company must match configuration")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("explicit company connector required")
    if (
        operator == reviewer
        or operator not in config.principals
        or reviewer not in config.principals
    ):
        raise BridgeError("separate configured operator and reviewer required")

    credentials = read_json(request["credentials"])
    if not isinstance(credentials, dict):
        raise BridgeError("credential object required")
    names = [
        connector.password_env,
        config.principals[operator]["token_env"],
        config.principals[reviewer]["token_env"],
    ]
    secrets_ok = all(_valid_secret(credentials, name) for name in names)
    policy = config.companies[company]
    operator_grants = set(config.principals[operator]["companies"].get(company, []))
    reviewer_grants = set(config.principals[reviewer]["companies"].get(company, []))

    qwc = _bounded_xml(request["qwc"])
    owner_id, file_id = qwc.findtext("OwnerID", ""), qwc.findtext("FileID", "")
    checks = {
        "company_file_exists": Path(target["company_file"]).is_file(),
        "company_identity_bound": connector.identity_sha256 != "0" * 64,
        "credentials_complete": secrets_ok,
        "credentials_distinct": secrets_ok
        and len({credentials[name] for name in names}) == len(names),
        "operator_workflow_permissions": operator_grants >= WORKFLOW_PERMISSIONS,
        "independent_reviewer": (
            policy.approval_required
            and not policy.allow_self_approval
            and "approve" in reviewer_grants
        ),
        "selected_entry_mappings": all(bool(getattr(policy, name)) for name in MAPPING_GROUPS),
        "selected_entry_gates": all(bool(getattr(policy, name)) for name in ENTRY_GATES),
        "qwc_company_connector": qwc.findtext("UserName") == connector_id,
        "qwc_https_endpoint": (qwc.findtext("AppURL") or "").startswith("https://")
        and (qwc.findtext("AppURL") or "").endswith("/qbwc"),
        "qwc_bridge_gated_access": qwc.findtext("IsReadOnly", "").lower() == "false",
        "qwc_stable_unique_ids": (
            owner_id.startswith("{")
            and file_id.startswith("{")
            and owner_id != file_id
            and owner_id != "{00000000-0000-0000-0000-000000000000}"
            and file_id != "{00000000-0000-0000-0000-000000000000}"
        ),
    }
    checks.update(_hermes_checks(request["hermes_bundle"], config, company, operator, reviewer))
    checks.update(_state_checks(config, company, connector_id))
    pending = [name for name, passed in checks.items() if not passed]
    return {
        "deployment_complete": not pending,
        "checks": checks,
        "pending": pending,
        "accounting_writes": 0,
        "posting_changed": False,
        "scope": "offline deployment walkthrough and retained connection evidence",
    }
