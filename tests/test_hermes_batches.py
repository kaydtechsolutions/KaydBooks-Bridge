"""Exact human review, QBWC lifecycle, and uncertain delivery regression checks."""

# ruff: noqa: F401,F811
import json

import pytest

from kaydbooks_bridge import hermes_batches as batches
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.hermes_tools import Tools
from kaydbooks_bridge.qbwc_posting import recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.web_ui import check_masters, manual
from test_hermes_qbwc import (
    authenticate,
    call,
    commercial,
    direct,
    discovery_setup,
    journal_case,
    receipt_case,
    receive,
    send,
    service,
    setup_invoice,
)
from test_qbwc_journals import Session


def test_batch_capacity_covers_thirty_entry_source_batch():
    assert batches.MAX_BATCH_ENTRIES == 30
    assert batches.MAX_BATCH_MANIFEST_BYTES >= 18000


@pytest.fixture
def reviewed(journal_case, monkeypatch):
    path, token, payload = journal_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"].update(approval_required=True, allow_self_approval=False)
    reviewer = "separate-reviewer-" + "x" * 32
    monkeypatch.setenv("KAYDBOOKS_BATCH_REVIEWER", reviewer)
    raw["principals"]["batch-reviewer"] = {
        "token_env": "KAYDBOOKS_BATCH_REVIEWER",
        "companies": {"company-a": ["approve"]},
    }
    path.write_text(json.dumps(raw))
    bridge = Bridge(path)
    sim = Session()
    args = (bridge, token, "company-a", "journal.create", "connector-company-a", payload)
    check_masters(*args)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    checked = check_masters(*args)
    policy = Config.load(path).companies["company-a"]
    job = manual(
        bridge,
        token,
        "company-a",
        "batch-one",
        policy.sources[0],
        "journal.create",
        payload,
        checked["evidence"],
    )
    bridge.action(token, "company-a", job["id"], "validate")
    batch = batches.create(bridge, token, "company-a", [job["id"]])
    return bridge, token, reviewer, job["id"], sim, batch


def confirm(case):
    b, t, r, j, sim, batch = case
    ident = batch["batch_id"]
    claimed = batches.claim_delivery(b, t, "company-a", ident, "preview")
    assert claimed["text"] == batch["preview"]
    batches.acknowledge_delivery(b, t, "company-a", ident, "preview", "provider-preview")
    return batches.confirm(
        b,
        t,
        "company-a",
        ident,
        batch["preview"].split()[-1],
        reviewer_token=r,
        sender="operator",
        event_id="event-1",
    )


@pytest.mark.parametrize("lost", [False, True])
def test_batch_posts_once_after_exact_review_and_reports_readback(reviewed, lost):
    b, t, r, j, sim, batch = reviewed
    ident = batch["batch_id"]
    assert batches.create(b, t, "company-a", [j])["batch_id"] == ident
    with pytest.raises(BridgeError, match="confirmation required"):
        batches.advance(b, t, "company-a", ident, reviewer_token=r)
    assert confirm(reviewed)["confirmed"]
    b.pause(t, "company-a", True)
    with pytest.raises(BridgeError, match="paused"):
        batches.advance(b, t, "company-a", ident, reviewer_token=r)
    b.pause(t, "company-a", False)
    batches.advance(b, t, "company-a", ident, reviewer_token=r)
    batches.advance(b, t, "company-a", ident, reviewer_token=r)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    result = sim.write(send(svc, ticket))
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        assert batches.status(b, t, "company-a", ident)["held"]
        assert batches.advance(b, t, "company-a", ident, reviewer_token=r)["held"]
        recover(b, t, "company-a", j)
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    else:
        assert receive(svc, ticket, result) == 75
    assert not batches.status(b, t, "company-a", ident)["held"]
    with pytest.raises(BridgeError, match="not ready"):
        batches.claim_delivery(b, t, "company-a", ident, "result")
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    assert batches.advance(b, t, "company-a", ident, reviewer_token=r)["complete"]
    result = batches.claim_delivery(b, t, "company-a", ident, "result")
    assert "SYN-JR-001 | journal.create | verified" in result["text"]
    assert batches.claim_delivery(b, t, "company-a", ident, "result") == {"claimed": False}
    batches.acknowledge_delivery(b, t, "company-a", ident, "result", "provider-result")
    assert batches.status(b, t, "company-a", ident)["deliveries"] == {
        "preview": True,
        "result": True,
    }
    assert sim.writes == 1 and b.audit(t, "company-a")["valid"]


@pytest.mark.parametrize("fault", ["wrong-code", "same-reviewer", "unsent", "expired", "policy"])
def test_untrusted_confirmation_cannot_dispatch(reviewed, fault):
    b, t, r, j, sim, batch = reviewed
    ident = batch["batch_id"]
    code = batch["preview"].split()[-1]
    if fault != "unsent":
        batches.claim_delivery(b, t, "company-a", ident, "preview")
        batches.acknowledge_delivery(b, t, "company-a", ident, "preview", "preview-id")
    if fault == "wrong-code":
        code = "wrong"
    if fault == "same-reviewer":
        r = t
    if fault == "expired":
        now = b.clock()
        b.clock = lambda: now + 901
    if fault == "policy":
        raw = json.loads(b.config_path.read_text())
        raw["companies"]["company-a"]["sample_journal_posting"]["max_entries"] = 3
        b.config_path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        batches.confirm(
            b, t, "company-a", ident, code, reviewer_token=r, sender="operator", event_id="event"
        )
    assert b.status(t, "company-a", j)["state"] == "validated" and sim.writes == 0


def test_unknown_delivery_is_not_resent_and_confirmation_is_idempotent(reviewed):
    b, t, r, j, sim, batch = reviewed
    ident = batch["batch_id"]
    confirm(reviewed)
    assert batches.confirm(
        b,
        t,
        "company-a",
        ident,
        batch["preview"].split()[-1],
        reviewer_token=r,
        sender="operator",
        event_id="event-1",
    )["already_confirmed"]
    assert batches.claim_delivery(b, t, "company-a", ident, "preview") == {"claimed": False}
    with pytest.raises(BridgeError, match="claimed first"):
        batches.acknowledge_delivery(b, t, "company-a", ident, "result", "invented-id")
    with pytest.raises(BridgeError):
        batches.status(b, t, "company-b", ident)


def test_human_delivery_observation_never_fabricates_provider_ack_or_approval(reviewed):
    b, t, r, j, sim, batch = reviewed
    ident = batch["batch_id"]
    batches.claim_delivery(b, t, "company-a", ident, "preview")
    batches.observe_preview(b, t, "company-a", ident, "sha256:retained-user-receipt-screenshot")
    state = batches.status(b, t, "company-a", ident)
    assert state["preview_observed"] and not state["confirmed"]
    assert state["deliveries"] == {"preview": False}
    assert batches.confirm(
        b,
        t,
        "company-a",
        ident,
        batch["preview"].split()[-1],
        reviewer_token=r,
        sender="operator",
        event_id="actual-native-reply",
    )["confirmed"]
    assert sim.writes == 0


def test_fresh_revision_requires_a_new_batch_and_confirmation(reviewed):
    import base64

    from kaydbooks_bridge.documents import capture

    b, t, r, j, sim, batch = reviewed
    parent = b.status(t, "company-a", j)
    source = capture(
        b,
        t,
        "company-a",
        parent["source"]["namespace"],
        "corrected-upload",
        "application/json",
        base64.b64encode(json.dumps(parent["payload"]).encode()).decode(),
    )
    args = {
        "parent_id": j,
        "parent_fingerprint": parent["fingerprint"],
        "reason": "Refresh expired review using newly checked evidence",
        "document_id": source["document_id"],
        "idempotency_key": "fresh-batch-revision",
        "payload": parent["payload"],
        "confidence": parent["source"]["original_values"]["confidence"],
        "master_evidence": check_masters(
            b, t, "company-a", "journal.create", "connector-company-a", parent["payload"]
        )["evidence"],
    }
    tools = Tools(b.config_path, t)
    child = tools.call("qbwc_entry_v1", "company-a", {"action": "revise", "parameters": args})
    b.action(t, "company-a", child["id"], "validate")
    fresh = batches.create(b, t, "company-a", [child["id"]])
    assert fresh["batch_id"] != batch["batch_id"] and not fresh["confirmed"]
    with pytest.raises(BridgeError):
        b.action(r, "company-a", j, "approve")
    assert sim.writes == 0
