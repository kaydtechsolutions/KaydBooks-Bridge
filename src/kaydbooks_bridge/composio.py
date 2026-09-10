"""Company-scoped, approved Composio tool execution with no blind retry."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .config import BridgeError, outside_repository, strict_keys
from .service import Bridge, audited
from .validation import canonical, digest

API_ROOT = "https://backend.composio.dev/api/v3.1/tools/execute/"
STATES = frozenset({"approved", "pending-approval", "in-flight", "completed", "failed", "unknown"})


def load_policy(path):
    value = json.loads(outside_repository(Path(path)).read_text(encoding="utf-8"))
    strict_keys(value, {"schema_version", "companies"})
    if value["schema_version"] != 1 or not isinstance(value["companies"], dict):
        raise BridgeError("invalid Composio policy")
    for company, rule in value["companies"].items():
        strict_keys(rule, {"user_id", "tools"})
        if (
            not isinstance(company, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", company)
            or not isinstance(rule["user_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", rule["user_id"])
            or not isinstance(rule["tools"], dict)
        ):
            raise BridgeError("invalid Composio company policy")
        for slug, tool in rule["tools"].items():
            strict_keys(tool, {"version", "mode", "connected_account_id_env"})
            if (
                not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", slug)
                or not re.fullmatch(r"[0-9]{8}_[0-9]{2}", tool["version"])
                or tool["mode"] not in {"read", "write"}
                or not re.fullmatch(
                    r"KAYDBOOKS_COMPOSIO_[A-Z0-9_]+", tool["connected_account_id_env"]
                )
            ):
                raise BridgeError("invalid Composio tool policy")
    return value


def schema(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS composio_requests (
        id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, tool TEXT NOT NULL,
        version TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('read','write')),
        arguments TEXT NOT NULL, fingerprint TEXT NOT NULL, requester TEXT NOT NULL,
        approver TEXT, state TEXT NOT NULL CHECK(state IN
        ('approved','pending-approval','in-flight','completed','failed','unknown')),
        result_sha256 TEXT, log_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS composio_no_delete BEFORE DELETE ON composio_requests
        BEGIN SELECT RAISE(ABORT,'Composio evidence is immutable'); END"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS composio_identity_immutable BEFORE UPDATE OF
        id,idempotency_key,tool,version,mode,arguments,fingerprint,requester,created_at
        ON composio_requests BEGIN SELECT RAISE(ABORT,'Composio request is immutable'); END"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS composio_terminal_guard BEFORE UPDATE OF state
        ON composio_requests WHEN OLD.state IN ('completed','failed','unknown')
        BEGIN SELECT RAISE(ABORT,'Composio outcome cannot be retried'); END"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS composio_transition_guard BEFORE UPDATE OF state
        ON composio_requests WHEN NOT (
        (OLD.state='pending-approval' AND NEW.state='approved') OR
        (OLD.state='approved' AND NEW.state='in-flight') OR
        (OLD.state='in-flight' AND NEW.state IN ('completed','failed','unknown')))
        BEGIN SELECT RAISE(ABORT,'invalid Composio state transition'); END"""
    )


def _rule(config, policy_path, company, tool):
    policy = load_policy(policy_path)
    if company not in config.companies:
        raise BridgeError("company unavailable")
    company_rule = policy["companies"].get(company)
    if company_rule is None or tool not in company_rule["tools"]:
        raise BridgeError("Composio tool is not assigned to this company")
    return company_rule, company_rule["tools"][tool]


def _arguments(value):
    if not isinstance(value, dict):
        raise BridgeError("Composio arguments must be an object")
    encoded = canonical(value)
    if len(encoded.encode()) > 65536:
        raise BridgeError("Composio arguments exceed the limit")

    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise BridgeError("invalid Composio argument key")
                inspect(child)
        elif isinstance(item, list):
            if len(item) > 1000:
                raise BridgeError("Composio argument list exceeds the limit")
            for child in item:
                inspect(child)
        elif isinstance(item, str) and (
            item.startswith(("file:", "/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", item)
        ):
            raise BridgeError("local file transfer through Composio is disabled")

    inspect(value)
    return encoded


@audited
def prepare(bridge, token, company, policy_path, tool, arguments, idempotency_key):
    config, actor, _, store = bridge._context(token, company, "prepare")
    if not isinstance(idempotency_key, str) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{8,128}", idempotency_key
    ):
        raise BridgeError("explicit Composio idempotency key required")
    _, rule = _rule(config, policy_path, company, tool)
    encoded = _arguments(arguments)
    fingerprint = digest(
        {"company": company, "tool": tool, "version": rule["version"], "arguments": arguments}
    )
    now = bridge.clock()
    state = "approved" if rule["mode"] == "read" else "pending-approval"
    with store.transaction() as db:
        schema(db)
        prior = db.execute(
            "SELECT * FROM composio_requests WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise BridgeError("Composio idempotency key conflicts")
            return _status(prior)
        request_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO composio_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request_id,
                idempotency_key,
                tool,
                rule["version"],
                rule["mode"],
                encoded,
                fingerprint,
                actor,
                None,
                state,
                None,
                None,
                now,
                now,
            ),
        )
        store.event(
            db,
            now,
            actor,
            None,
            "composio_request_prepared",
            {
                "request_id": request_id,
                "tool": tool,
                "mode": rule["mode"],
                "fingerprint": fingerprint,
            },
        )
        return _status(
            db.execute("SELECT * FROM composio_requests WHERE id=?", (request_id,)).fetchone()
        )


@audited
def approve(bridge, token, company, request_id):
    _, actor, _, store = bridge._context(token, company, "approve")
    with store.transaction() as db:
        schema(db)
        row = db.execute("SELECT * FROM composio_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or row["state"] != "pending-approval" or row["requester"] == actor:
            raise BridgeError("independent pending Composio approval required")
        db.execute(
            "UPDATE composio_requests SET approver=?,state='approved',updated_at=? WHERE id=?",
            (actor, bridge.clock(), request_id),
        )
        store.event(
            db, bridge.clock(), actor, None, "composio_request_approved", {"request_id": request_id}
        )
        return _status(
            db.execute("SELECT * FROM composio_requests WHERE id=?", (request_id,)).fetchone()
        )


def http_execute(tool, payload, api_key, *, timeout=30):
    request = urllib.request.Request(
        API_ROOT + tool,
        data=canonical(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise BridgeError("Composio returned a definitive failure")
        content = response.read(1_048_577)
    if len(content) > 1_048_576:
        raise BridgeError("Composio response exceeds the limit")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise BridgeError("invalid Composio response")
    return value


@audited
def execute(bridge, token, company, policy_path, request_id, *, transport=http_execute):
    config, actor, _, store = bridge._context(token, company, "submit")
    with store.transaction() as db:
        schema(db)
        row = db.execute("SELECT * FROM composio_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or row["state"] != "approved" or row["requester"] != actor:
            raise BridgeError("owned approved Composio request required")
        company_rule, rule = _rule(config, policy_path, company, row["tool"])
        if rule["version"] != row["version"] or rule["mode"] != row["mode"]:
            raise BridgeError("Composio policy changed; prepare a new request")
        account = os.environ.get(rule["connected_account_id_env"], "")
        api_key = os.environ.get("KAYDBOOKS_COMPOSIO_API_KEY", "")
        if len(account) < 8 or len(api_key) < 20:
            raise BridgeError("Composio connection is unavailable")
        payload = {
            "arguments": json.loads(row["arguments"]),
            "user_id": company_rule["user_id"],
            "connected_account_id": account,
            "version": row["version"],
        }
        db.execute(
            "UPDATE composio_requests SET state='in-flight',updated_at=? WHERE id=?",
            (bridge.clock(), request_id),
        )
        store.event(
            db, bridge.clock(), actor, None, "composio_dispatch_intent", {"request_id": request_id}
        )
    try:
        response = transport(row["tool"], payload, api_key)
        successful = response.get("successful") is True
        state = "completed" if successful else "failed"
        response_hash = digest(response)
        log_id = response.get("log_id")
        if log_id is not None and (not isinstance(log_id, str) or len(log_id) > 256):
            log_id = None
    except (BridgeError, OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        response, state, response_hash, log_id = None, "unknown", None, None
    with store.transaction() as db:
        current = db.execute(
            "SELECT state FROM composio_requests WHERE id=?", (request_id,)
        ).fetchone()
        if current is None or current["state"] != "in-flight":
            raise BridgeError("Composio dispatch evidence changed")
        db.execute(
            "UPDATE composio_requests SET state=?,result_sha256=?,log_id=?,updated_at=? WHERE id=?",
            (state, response_hash, log_id, bridge.clock(), request_id),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "composio_dispatch_result",
            {
                "request_id": request_id,
                "state": state,
                "result_sha256": response_hash,
                "log_id": log_id,
            },
        )
    if state == "unknown":
        raise BridgeError("Composio outcome is unknown; inspect provider logs and do not retry")
    return {"request": status(bridge, token, company, request_id), "response": response}


def _status(row):
    return {
        key: row[key]
        for key in (
            "id",
            "tool",
            "version",
            "mode",
            "fingerprint",
            "requester",
            "approver",
            "state",
            "result_sha256",
            "log_id",
            "created_at",
            "updated_at",
        )
    }


@audited
def status(bridge, token, company, request_id):
    _, _, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        schema(db)
        row = db.execute("SELECT * FROM composio_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise BridgeError("Composio request not found")
        return _status(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("KAYDBOOKS_CONFIG"))
    parser.add_argument("--policy", default=os.environ.get("KAYDBOOKS_COMPOSIO_POLICY"))
    parser.add_argument("--company", required=True)
    parser.add_argument("action", choices=["prepare", "approve", "execute", "status"])
    parser.add_argument("input", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.config or not args.policy or args.input.stat().st_size > 65536:
            raise BridgeError("private bounded Composio configuration required")
        values = json.loads(args.input.read_text(encoding="utf-8"))
        token = os.environ.get("KAYDBOOKS_TOKEN", "")
        bridge = Bridge(args.config)
        if args.action == "prepare":
            strict_keys(values, {"tool", "arguments", "idempotency_key"})
            result = prepare(bridge, token, args.company, args.policy, **values)
        else:
            strict_keys(values, {"request_id"})
            result = (
                globals()[args.action](bridge, token, args.company, **values)
                if args.action != "execute"
                else execute(bridge, token, args.company, args.policy, **values)
            )
        print(canonical(result))
        return 0
    except (BridgeError, OSError, ValueError, TypeError, KeyError):
        print(canonical({"error": "Composio request rejected; inspect company audit"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
