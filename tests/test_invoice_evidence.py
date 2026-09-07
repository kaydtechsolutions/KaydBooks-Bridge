"""Preparation must resolve fresh server evidence across both transports."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_direct_sdk import direct  # noqa: F401
from test_invoice_compatibility import exchange, run, setup_invoice  # noqa: F401
from test_qbwc_discovery import (  # noqa: F401
    PASSWORD_A,
    authenticate,
    call,
    discovery_setup,
    receive,
)
from test_qbwc_invoices import queue, response_for, send


@pytest.fixture(params=["direct-sdk", "qbwc"])
def linked(setup_invoice, request, tmp_path):  # noqa: F811
    path, token, payload = setup_invoice
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] += ["prepare", "submit", "simulate"]
    path.write_text(json.dumps(raw))
    if request.param == "direct-sdk":
        run(setup_invoice)
        evidence_id = "921"
    else:
        svc = queue(setup_invoice)
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, response_for(send(svc, ticket), tmp_path)) == 100
        assert call(svc, "closeConnection", ticket=ticket) == "OK"
        evidence_id = "check-one"
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope["payload"] = payload
    envelope["master_evidence"] = {
        "transport": request.param,
        "connector": "connector-company-a",
        "id": evidence_id,
    }
    return Bridge(path), token, envelope, raw


def test_prepare_restart_duplicate_and_audit(linked):
    bridge, token, envelope, _ = linked
    job = bridge.prepare(token, "company-a", envelope)
    assert (
        job["state"] == "draft"
        and job["master_evidence"]["reference"] == envelope["master_evidence"]
    )
    restarted = Bridge(bridge.config_path)
    assert restarted.prepare(token, "company-a", envelope) == job
    assert restarted.action(token, "company-a", job["id"], "validate")["state"] == "validated"
    config = Config.load(bridge.config_path)
    store = Store(config.root, "company-a")
    with store.transaction() as db:
        assert store.verify_audit(db)
        for sql in (
            "DELETE FROM invoice_evidence_links",
            "UPDATE invoice_evidence_links SET evidence='{}'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "claimed-time",
        "payload",
        "policy",
        "binding",
        "revocation",
        "wrong-company",
        "unknown",
        "transport",
    ],
)
def test_untrusted_or_changed_evidence_rejected(linked, change):
    bridge, token, envelope, raw = linked
    if change == "missing":
        envelope.pop("master_evidence")
    elif change == "claimed-time":
        envelope["master_evidence"]["observed_at"] = time.time()
    elif change == "payload":
        envelope["payload"]["lines"][0]["amount"] = "2.00"
    elif change == "policy":
        raw["companies"]["company-a"]["account_roles"]["invoice_receivable"] = "changed-id"
    elif change == "binding":
        raw["connectors"]["connector-company-a"]["identity_sha256"] = "f" * 64
    elif change == "revocation":
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "validate"
        )
    elif change == "wrong-company":
        envelope["master_evidence"]["connector"] = "connector-company-b"
    elif change == "unknown":
        envelope["master_evidence"]["id"] = (
            "998" if envelope["master_evidence"]["transport"] == "direct-sdk" else "missing"
        )
    else:
        envelope["master_evidence"]["transport"] = "client-verified"
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        bridge.prepare(token, "company-a", envelope)
    assert bridge.status(token, "company-a")["jobs"] == []


@pytest.mark.parametrize("stage", ["prepare", "validate", "approve", "submit", "simulate"])
def test_expiry_checked_at_each_boundary(linked, stage):
    bridge, token, envelope, raw = linked
    raw["companies"]["company-a"]["approval_required"] = False
    Path(bridge.config_path).write_text(json.dumps(raw))
    job = bridge.prepare(token, "company-a", envelope)
    if stage in ("approve", "submit", "simulate"):
        bridge.action(token, "company-a", job["id"], "validate")
    if stage == "simulate":
        bridge.action(token, "company-a", job["id"], "submit")
    bridge.clock = lambda: job["master_evidence"]["observed_at"] + 900
    if stage == "approve":
        actor = next(iter(raw["principals"]))
        raw["principals"][actor]["companies"]["company-a"].append("approve")
        Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="stale"):
        if stage == "prepare":
            bridge.prepare(token, "company-a", envelope)
        elif stage == "simulate":
            bridge.simulate(token, "company-a")
        else:
            bridge.action(token, "company-a", job["id"], stage)


def test_future_timestamp_and_reduced_ttl_rejected(linked):
    bridge, token, envelope, raw = linked
    job = bridge.prepare(token, "company-a", envelope)
    at = job["master_evidence"]["observed_at"]
    bridge.clock = lambda: at - 1
    with pytest.raises(BridgeError, match="stale"):
        bridge.prepare(token, "company-a", envelope)
    raw["companies"]["company-a"]["invoice_evidence_max_age_seconds"] = 60
    Path(bridge.config_path).write_text(json.dumps(raw))
    bridge.clock = lambda: at + 61
    with pytest.raises(BridgeError, match="stale"):
        bridge.action(token, "company-a", job["id"], "validate")


def test_refresh_preserves_job_and_clears_approval(linked, monkeypatch):
    bridge, token, envelope, raw = linked
    job = bridge.prepare(token, "company-a", envelope)
    bridge.action(token, "company-a", job["id"], "validate")
    approver_token = "synthetic-fresh-evidence-approver-" + "x" * 32
    monkeypatch.setenv("KAYDBOOKS_EVIDENCE_APPROVER", approver_token)
    raw["principals"]["evidence-approver"] = {
        "token_env": "KAYDBOOKS_EVIDENCE_APPROVER",
        "companies": {"company-a": ["approve"]},
    }
    Path(bridge.config_path).write_text(json.dumps(raw))
    bridge.action(approver_token, "company-a", job["id"], "approve")
    bridge.action(token, "company-a", job["id"], "submit")
    discover(
        DurableQBWCDiscoveryService.from_path(bridge.config_path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "922",
        invoice_check=envelope["payload"],
        exchange=exchange(),
    )
    envelope["master_evidence"] = {
        "transport": "direct-sdk",
        "connector": "connector-company-a",
        "id": "922",
    }
    refreshed = bridge.prepare(token, "company-a", envelope)
    assert refreshed["id"] == job["id"] and refreshed["state"] == "draft"
    assert refreshed["approval_by"] is None and refreshed["approval_hash"] is None
    assert len(bridge.status(token, "company-a")["jobs"]) == 1


def test_sdk_replay_cannot_renew_evidence_age(linked):
    bridge, token, envelope, _ = linked
    if envelope["master_evidence"]["transport"] != "direct-sdk":
        return
    original = bridge.prepare(token, "company-a", envelope)
    discover(
        DurableQBWCDiscoveryService.from_path(bridge.config_path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "921",
        invoice_check=envelope["payload"],
        exchange=lambda *_: pytest.fail("unexpected SDK dispatch"),
    )
    assert (
        bridge.prepare(token, "company-a", envelope)["master_evidence"]
        == original["master_evidence"]
    )
    bridge.clock = lambda: original["master_evidence"]["observed_at"] + 901
    with pytest.raises(BridgeError, match="stale"):
        bridge.prepare(token, "company-a", envelope)


@pytest.mark.parametrize("change", ["owner", "mapping-removed", "permission"])
def test_link_cannot_bypass_current_ownership_or_policy(linked, monkeypatch, change):
    bridge, token, envelope, raw = linked
    job = bridge.prepare(token, "company-a", envelope)
    if change == "owner":
        token = "synthetic-other-evidence-owner-" + "x" * 32
        monkeypatch.setenv("KAYDBOOKS_OTHER_OWNER", token)
        raw["principals"]["other-owner"] = {
            "token_env": "KAYDBOOKS_OTHER_OWNER",
            "companies": {"company-a": ["prepare", "read", "validate"]},
        }
    elif change == "mapping-removed":
        raw["companies"]["company-a"].pop("invoice_masters")
    else:
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("read")
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        if change == "owner":
            bridge.prepare(token, "company-a", envelope)
        else:
            bridge.action(token, "company-a", job["id"], "validate")


def test_terminal_job_cannot_refresh_evidence(linked):
    bridge, token, envelope, raw = linked
    raw["companies"]["company-a"]["approval_required"] = False
    Path(bridge.config_path).write_text(json.dumps(raw))
    job = bridge.prepare(token, "company-a", envelope)
    bridge.action(token, "company-a", job["id"], "validate")
    bridge.action(token, "company-a", job["id"], "submit")
    assert bridge.simulate(token, "company-a")["state"] == "verified"
    discover(
        DurableQBWCDiscoveryService.from_path(bridge.config_path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "923",
        invoice_check=envelope["payload"],
        exchange=exchange(),
    )
    envelope["master_evidence"] = {
        "transport": "direct-sdk",
        "connector": "connector-company-a",
        "id": "923",
    }
    with pytest.raises(BridgeError, match="before dispatch"):
        bridge.prepare(token, "company-a", envelope)


@pytest.mark.parametrize("age", [False, 0, 59, 86401, "900"])
def test_invalid_age_policy(linked, age):
    bridge, _, _, raw = linked
    raw["companies"]["company-a"]["invoice_evidence_max_age_seconds"] = age
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="age"):
        Config.load(bridge.config_path)
