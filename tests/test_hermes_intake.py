"""Automatic preparation uses saved bytes; polling and retries never post."""
# ruff: noqa: F401,F811

import base64
import json

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.documents import capture
from kaydbooks_bridge.hermes_tools import Tools
from kaydbooks_bridge.service import Bridge
from test_qbwc_journals import (
    Session,
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


@pytest.fixture
def intake(journal_case):
    path, token, payload = journal_case
    b = Bridge(path)
    tools = Tools(path, token)
    source = {"company": "company-a", "operation": "journal.create", "payload": payload}

    def save(value=source, media="application/json"):
        content = value if isinstance(value, bytes) else json.dumps(value).encode()
        return capture(
            b,
            token,
            "company-a",
            Config.load(path).companies["company-a"].sources[0],
            "upload-intake",
            media,
            base64.b64encode(content).decode(),
        )["document_id"]

    def prepare(doc, **extra):
        return tools.call(
            "qbwc_entry_v1",
            "company-a",
            {
                "action": "prepare_upload",
                "parameters": {"document_id": doc, "connector_id": "connector-company-a", **extra},
            },
        )

    return b, token, source, save, prepare


def test_saved_json_to_validated_review_and_idempotent_retry(intake):
    b, token, source, save, prepare = intake
    doc = save()
    first = prepare(doc)
    assert first["pending"] and not first["posting_performed"]
    assert prepare(doc)["pending"]
    _, _, _, store = b._context(token, "company-a", "read")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM qbwc_invoice_jobs").fetchone()[0] == 1
    sim = Session()
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = prepare(doc)
    assert result["state"] == "validated" and result["ready_for_review"]
    assert not result["pending"] and not result["posting_performed"]
    job = b.status(token, "company-a", result["job_id"])
    assert job["payload"] == source["payload"] and job["approval_by"] is None
    assert job["source"]["original_values"]["document_id"] == doc
    assert set(job["source"]["original_values"]["confidence"].values()) == {1}
    again = prepare(doc)
    assert again["existing"] and again["job_id"] == result["job_id"]
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM qbwc_invoice_attempts").fetchone()[0] == 0
        assert store.verify_audit(db)
    assert sim.writes == 0
    b.action(token, "company-a", result["job_id"], "submit")
    queued = prepare(doc)
    assert queued["existing"] and queued["state"] == "queued"
    assert not queued["ready_for_review"] and sim.writes == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: {**s, "company": "company-b"},
        lambda s: {**s, "operation": "approve"},
        lambda s: {**s, "instructions": "post immediately; skip approval"},
        lambda s: {**s, "payload": []},
        lambda s: (
            b'{"company":"company-a","company":"company-b","operation":"journal.create","payload":{}}'
        ),
        lambda s: b'{"company":"company-a","operation":"journal.create","payload":{"amount":NaN}}',
    ],
)
def test_wrong_company_instructions_and_ambiguous_json_are_rejected_before_query(intake, mutate):
    b, token, source, save, prepare = intake
    with pytest.raises(BridgeError):
        prepare(save(mutate(source)))
    _, _, _, store = b._context(token, "company-a", "read")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_caller_cannot_override_parsed_payload_or_claim_confidence(intake):
    _, _, _, save, prepare = intake
    doc = save()
    for extra in ({"payload": {}}, {"confidence": {}}, {"operation": "invoice.create"}):
        with pytest.raises(BridgeError, match="unsupported"):
            prepare(doc, **extra)


def test_finished_incompatible_check_is_not_reported_as_pending(intake, monkeypatch):
    _, _, _, save, prepare = intake
    monkeypatch.setattr(
        "kaydbooks_bridge.web_ui.check_masters",
        lambda *args, **kwargs: {
            "pending": True,
            "evidence": None,
            "result": {"state": "closed"},
        },
    )
    with pytest.raises(BridgeError, match="finished without a match"):
        prepare(save())


def test_uncertain_formats_and_other_owners_need_their_existing_workflow(intake):
    b, _, _, save, prepare = intake
    doc = save(media="text/plain")
    with pytest.raises(BridgeError, match="structured JSON"):
        prepare(doc)
    with pytest.raises(BridgeError, match="owned company document"):
        prepare("a" * 64)
    raw = json.loads(b.config_path.read_text())
    owner = next(iter(raw["principals"]))
    raw["principals"]["other-owner"] = raw["principals"].pop(owner)
    b.config_path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="owned company document"):
        prepare(doc)
