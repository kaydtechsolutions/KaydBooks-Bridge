"""Corrections preserve sources and lineage while preventing old approvals or duplicate dispatch."""

# ruff: noqa: F811
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from kaydbooks_bridge import documents
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_bridge import TOKENS, setup  # noqa: F401
from test_documents_tools import document


def correction(
    setup,
    parent,
    source,
    *,
    key="revision-one",
    amount="100.00",
    confidence=1,
    reason="Correct extracted amount",
    token=None,
):
    payload = copy.deepcopy(parent["payload"])
    payload["lines"][0]["amount"] = amount
    return documents.revise(
        setup[0],
        token or TOKENS["preparer-a"],
        "company-a",
        parent["id"],
        parent["fingerprint"],
        reason,
        source["document_id"],
        key,
        payload,
        {k: confidence for k in documents.fields(payload)},
    )


def test_approved_queued_revision_preserves_original_and_needs_fresh_approval(setup):
    bridge, path, _, _ = setup
    _, source, parent = document(setup)
    bridge.action(TOKENS["preparer-a"], "company-a", parent["id"], "validate")
    bridge.action(TOKENS["approver-a"], "company-a", parent["id"], "approve")
    bridge.action(TOKENS["preparer-a"], "company-a", parent["id"], "submit")
    child = correction(setup, parent, source)
    assert child["state"] == "draft" and child["approval_by"] is None and child["attempt"] is None
    assert child["revision"]["root_id"] == parent["id"] and child["revision"]["number"] == 1
    old = Bridge(path).status(TOKENS["preparer-a"], "company-a", parent["id"])
    assert old["state"] == "superseded" and old["superseded_by"] == child["id"]
    assert old["payload"] == parent["payload"] and old["source"] == parent["source"]
    assert child["source"]["original_values"]["document_id"] == source["document_id"]
    assert child["fingerprint"] != parent["fingerprint"]
    for action in ("validate", "approve", "submit"):
        with pytest.raises(BridgeError):
            bridge.action(TOKENS["approver-a"], "company-a", parent["id"], action)
    with pytest.raises(BridgeError):
        bridge.preview(TOKENS["preparer-a"], "company-a", parent["id"])
    bridge.action(TOKENS["preparer-a"], "company-a", child["id"], "validate")
    with pytest.raises(BridgeError, match="approval required"):
        bridge.action(TOKENS["preparer-a"], "company-a", child["id"], "submit")
    bridge.action(TOKENS["approver-a"], "company-a", child["id"], "approve")
    bridge.action(TOKENS["preparer-a"], "company-a", child["id"], "submit")
    assert bridge.simulate(TOKENS["operator-a"], "company-a")["id"] == child["id"]
    assert bridge.simulate(TOKENS["operator-a"], "company-a") is None
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]


def test_revision_retry_and_reimport_preserve_canonical_identity(setup):
    _, source, parent = document(setup)
    child = correction(setup, parent, source)
    assert correction(setup, parent, source)["id"] == child["id"]
    assert (
        documents.prepare(
            setup[0],
            TOKENS["preparer-a"],
            "company-a",
            source["document_id"],
            "reimport-new-key",
            child["payload"],
            {k: 1 for k in documents.fields(child["payload"])},
        )["id"]
        == child["id"]
    )
    with pytest.raises(BridgeError):
        documents.prepare(
            setup[0],
            TOKENS["preparer-a"],
            "company-a",
            source["document_id"],
            "old-reimport",
            parent["payload"],
            {k: 1 for k in documents.fields(parent["payload"])},
        )
    with pytest.raises(BridgeError):
        correction(setup, parent, source, key="another-key")
    second = correction(setup, child, source, key="revision-two", amount="90.00")
    assert second["revision"]["root_id"] == parent["id"] and second["revision"]["number"] == 2
    with pytest.raises(BridgeError):
        correction(setup, parent, source)


def test_revision_cannot_take_another_business_reference(setup):
    bridge, _, _, envelope = setup
    _, source, parent = document(setup)
    other = copy.deepcopy(envelope)
    other["payload"]["ref_number"] = "OTHER-1"
    other["source"]["reference"] = "other-doc"
    other["idempotency_key"] = "other-key"
    bridge.prepare(TOKENS["preparer-a"], "company-a", other)
    payload = copy.deepcopy(parent["payload"])
    payload["ref_number"] = "OTHER-1"
    with pytest.raises(BridgeError, match="another canonical"):
        documents.revise(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            parent["id"],
            parent["fingerprint"],
            "Correct reference",
            source["document_id"],
            "correction",
            payload,
            {k: 1 for k in documents.fields(payload)},
        )
    assert bridge.status(TOKENS["preparer-a"], "company-a", parent["id"])["state"] == "draft"


@pytest.mark.parametrize(
    "case", ["owner", "fingerprint", "no-change", "reason", "oversized-reason", "permission"]
)
def test_invalid_correction_preserves_parent(setup, case):
    bridge, path, raw, _ = setup
    _, source, parent = document(setup)
    actor = TOKENS["preparer-a"]
    reason = "Correction"
    amount = "100.00"
    parent = copy.deepcopy(parent)
    if case == "owner":
        actor = TOKENS["operator-a"]
    if case == "fingerprint":
        parent["fingerprint"] = "0" * 64
    if case == "no-change":
        amount = "125.00"
    if case == "reason":
        reason = " "
    if case == "oversized-reason":
        reason = " " * 1000 + "x"
    if case == "permission":
        raw["principals"]["preparer-a"]["companies"]["company-a"].remove("prepare")
        path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        correction(setup, parent, source, amount=amount, reason=reason, token=actor)
    assert bridge.status(TOKENS["preparer-a"], "company-a", parent["id"])["state"] == "draft"


@pytest.mark.parametrize("finish", [False, True])
def test_dispatched_or_posted_transaction_cannot_be_revised(setup, finish):
    bridge, _, _, _ = setup
    _, source, parent = document(setup)
    bridge.action(TOKENS["preparer-a"], "company-a", parent["id"], "validate")
    bridge.action(TOKENS["approver-a"], "company-a", parent["id"], "approve")
    bridge.action(TOKENS["preparer-a"], "company-a", parent["id"], "submit")
    if finish:
        bridge.simulate(TOKENS["operator-a"], "company-a")
    else:
        store = Store(Config.load(setup[1]).root, "company-a")
        with store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='in-flight',attempt='held',lease_until=123 WHERE id=?",
                (parent["id"],),
            )
    with pytest.raises(BridgeError, match="undispatched"):
        correction(setup, parent, source)


def test_superseded_database_row_and_lineage_are_immutable(setup):
    _, source, parent = document(setup)
    child = correction(setup, parent, source)
    store = Store(Config.load(setup[1]).root, "company-a")
    with store.transaction() as db:
        for sql, params in [
            ("UPDATE jobs SET state='validated' WHERE id=?", (parent["id"],)),
            ("DELETE FROM job_revisions", ()),
            ("UPDATE job_revisions SET reason='changed'", ()),
            ("DELETE FROM canonical_job_keys", ()),
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, params)
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert store.job(db, child["id"])["revision"]["parent_id"] == parent["id"]


def test_uncertain_correction_requires_new_source_review(setup):
    _, source, parent = document(setup)
    child = correction(setup, parent, source, confidence=0.5)
    with pytest.raises(BridgeError, match="source review"):
        setup[0].action(TOKENS["preparer-a"], "company-a", child["id"], "validate")
    assert parent["source"]["uncertain_fields"] == []


def test_concurrent_corrections_create_one_successor(setup):
    _, source, parent = document(setup)

    def run(i):
        try:
            return correction(
                setup, parent, source, key="revision-" + str(i), amount=str(100 - i) + ".00"
            )
        except BridgeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert sum(r is not None for r in results) == 1
    store = Store(Config.load(setup[1]).root, "company-a")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM job_revisions").fetchone()[0] == 1


def test_cli_revision_and_mcp_retry_share_one_lineage(setup, tmp_path, monkeypatch, capsys):
    from kaydbooks_bridge.cli import main
    from kaydbooks_bridge.hermes_tools import Tools

    _, source, parent = document(setup)
    payload = copy.deepcopy(parent["payload"])
    payload["lines"][0]["amount"] = "100.00"
    request = {
        "parent_fingerprint": parent["fingerprint"],
        "reason": "Correct amount",
        "document_id": source["document_id"],
        "idempotency_key": "cli-revision",
        "payload": payload,
        "confidence": {k: 1 for k in documents.fields(payload)},
    }
    f = tmp_path / "revision.json"
    f.write_text(json.dumps(request))
    monkeypatch.setenv("KAYDBOOKS_TOKEN", TOKENS["preparer-a"])
    assert (
        main(
            [
                "--config",
                str(setup[1]),
                "--company",
                "company-a",
                "revise-document",
                parent["id"],
                str(f),
            ]
        )
        == 0
    )
    child = json.loads(capsys.readouterr().out)
    duplicate = Tools(setup[1], TOKENS["preparer-a"]).call(
        "revise_document_v1", "company-a", {"parent_id": parent["id"], **request}
    )
    assert child["id"] == duplicate["id"]
