import copy
import json
import time
from pathlib import Path

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.service import Bridge
from test_bill_lookup import exact_case, exact_run  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401


@pytest.fixture
def case(exact_case, monkeypatch):  # noqa: F811
    path, token, payload = exact_case
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    policy = raw["companies"]["company-a"]
    policy["approval_required"] = True
    policy["sample_bill_posting"] = {
        "connector": "connector-company-a",
        "authorization": "Explicit synthetic sample only",
        "ref_prefix": "BILL-",
        "max_bills": 1,
        "expires_at": time.time() + 3600,
    }
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        operation="bill.create",
        payload=payload,
        master_evidence={
            "transport": "direct-sdk",
            "connector": "connector-company-a",
            "id": "993",
        },
    )
    envelope["source"]["namespace"] = policy["sources"][0]
    grant = {
        "id": "test-grant",
        "company": "company-a",
        "connector": "connector-company-a",
        "identity_sha256": raw["connectors"]["connector-company-a"]["identity_sha256"],
        "preparer": actor,
        "enabled": True,
        "activated_at": time.time() - 1,
        "expires_at": time.time() + 1800,
        "authorization": "User explicitly permits this exact synthetic test bill",
        "entries": [
            {
                "operation": "bill.create",
                "payload": copy.deepcopy(payload),
                "source_namespace": envelope["source"]["namespace"],
                "source_reference": envelope["source"]["reference"],
            }
        ],
    }
    raw["principals"]["sample-automation"] = {
        "token_env": "KAYDBOOKS_QUALIFICATION_TEST",
        "companies": {"company-a": ["read", "approve"]},
        "sample_qualification": grant,
    }
    monkeypatch.setenv("KAYDBOOKS_QUALIFICATION_TEST", "q" * 40)
    path.write_text(json.dumps(raw))
    exact_run(exact_case)
    b = Bridge(path)
    job = b.prepare(token, "company-a", envelope)
    b.action(token, "company-a", job["id"], "validate")
    return path, b, token, job["id"]


def change(path, fn):
    raw = json.loads(path.read_text())
    fn(raw)
    path.write_text(json.dumps(raw))


def test_delegated_approval_is_audited_and_submission_still_owned(case):
    _, b, token, job = case
    result = b.action("q" * 40, "company-a", job, "approve")
    assert result["approval_by"] == "sample-automation"
    with pytest.raises(BridgeError):
        b.action("q" * 40, "company-a", job, "submit")
    assert b.action(token, "company-a", job, "submit")["state"] == "queued"
    _, _, _, store = b._context(token, "company-a", "read")
    with store.transaction() as db:
        event = db.execute(
            "SELECT data FROM audit WHERE event='sample_qualification_approved'"
        ).fetchone()
        assert json.loads(event[0])["approval_kind"] == "operator-delegated-sample-automation"
        assert store.verify_audit(db)


@pytest.mark.parametrize(
    "mutation",
    ["amount", "source", "identity", "expired", "disabled", "quota", "preparer", "operation"],
)
def test_grant_cannot_approve_outside_exact_scope(case, mutation):
    path, b, token, job = case

    def mutate(raw):
        g = raw["principals"]["sample-automation"]["sample_qualification"]
        if mutation == "amount":
            g["entries"][0]["payload"]["lines"][0]["amount"] = "11.00"
        if mutation == "source":
            g["entries"][0]["source_reference"] = "other-source"
        if mutation == "identity":
            g["identity_sha256"] = "1" * 64
        if mutation == "expired":
            g.update(activated_at=time.time() - 200, expires_at=time.time() - 1)
        if mutation == "disabled":
            g["enabled"] = False
        if mutation == "quota":
            raw["companies"]["company-a"]["sample_bill_posting"]["max_bills"] = 2
        if mutation == "preparer":
            g["preparer"] = "someone-else"
        if mutation == "operation":
            g["entries"][0]["operation"] = "journal.create"

    change(path, mutate)
    with pytest.raises(BridgeError):
        b.action("q" * 40, "company-a", job, "approve")
    assert b.status(token, "company-a", job)["approval_by"] is None


def test_disabling_grant_after_approval_blocks_submit(case):
    path, b, token, job = case
    b.action("q" * 40, "company-a", job, "approve")
    change(
        path,
        lambda r: r["principals"]["sample-automation"]["sample_qualification"].update(
            enabled=False
        ),
    )
    with pytest.raises(BridgeError, match="disabled"):
        b.action(token, "company-a", job, "submit")


def test_grant_cannot_exceed_eight_hours(case):
    path, b, _, job = case
    raw = json.loads(path.read_text())
    g = raw["principals"]["sample-automation"]["sample_qualification"]
    g["expires_at"] = g["activated_at"] + 9 * 3600
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="eight hours"):
        Config.load(path)


def test_stale_evidence_still_blocks_automation(case):
    path, _, _, job = case
    b = Bridge(path, clock=lambda: time.time() + 1000)
    with pytest.raises(BridgeError, match="stale"):
        b.action("q" * 40, "company-a", job, "approve")


def test_grant_entry_cannot_be_used_for_second_job(case):
    from kaydbooks_bridge.sample_qualification import require_if_delegated

    _, b, token, job_id = case
    b.action("q" * 40, "company-a", job_id, "approve")
    config, _, policy, store = b._context(token, "company-a", "read")
    with store.transaction() as db:
        job = store.job(db, job_id)
        job["id"] = "1" * 32
        with pytest.raises(BridgeError, match="another job"):
            require_if_delegated(config, policy, store, db, job, "sample-automation", time.time())


def test_changed_manifest_blocks_final_dispatch(case):
    from kaydbooks_bridge.dispatch import require

    path, b, token, job_id = case
    b.action("q" * 40, "company-a", job_id, "approve")
    b.action(token, "company-a", job_id, "submit")
    change(
        path,
        lambda r: r["principals"]["sample-automation"]["sample_qualification"]["entries"][0].update(
            source_reference="changed"
        ),
    )
    config, actor, policy, store = b._context(token, "company-a", "read")
    with store.transaction() as db, pytest.raises(BridgeError, match="exact sample authorization"):
        require(config, actor, policy, store, db, store.job(db, job_id), time.time())


@pytest.mark.parametrize(
    "mutation", [None, "unverified", "wrong-company", "external", "wrong-kind", "other-txn"]
)
def test_payment_allocation_requires_exact_verified_bridge_receipt(case, mutation):
    from kaydbooks_bridge.sample_qualification import require_if_delegated

    path, b, token, job_id = case
    raw = json.loads(path.read_text())
    grant = raw["principals"]["sample-automation"]["sample_qualification"]
    entry = grant["entries"][0]
    entry.update(
        operation="customer-payment.create",
        allocation_job="2" * 32,
        payload={"allocations": [{"txn_id": "$verified-prerequisite", "amount": "10.00"}]},
    )
    raw["companies"]["company-a"]["sample_payment_posting"] = {
        "connector": "connector-company-a",
        "authorization": "Explicit sample payment test",
        "ref_prefix": "RV-",
        "max_payments": 1,
        "expires_at": time.time() + 3600,
    }
    path.write_text(json.dumps(raw))
    config, _, policy, store = b._context(token, "company-a", "read")
    prerequisite = {
        "state": "verified",
        "operation": "invoice.create",
        "submitter": grant["preparer"],
        "txn_id": "SAVED-001",
        "transaction_receipt": {
            "identity_sha256": grant["identity_sha256"],
            "bridge_dispatched": True,
        },
    }
    if mutation == "unverified":
        prerequisite["state"] = "posted-unverified"
    if mutation == "wrong-company":
        prerequisite["transaction_receipt"]["identity_sha256"] = "f" * 64
    if mutation == "external":
        prerequisite["transaction_receipt"]["bridge_dispatched"] = False
    if mutation == "wrong-kind":
        prerequisite["operation"] = "bill.create"

    class ReceiptStore:
        def job(self, db, key):
            assert key == "2" * 32
            return prerequisite

        def verify_audit(self, db):
            return store.verify_audit(db)

    with store.transaction() as db:
        job = store.job(db, job_id)
        job.update(
            operation="customer-payment.create",
            payload={
                "allocations": [
                    {
                        "txn_id": "OTHER-001" if mutation == "other-txn" else "SAVED-001",
                        "amount": "10.00",
                    }
                ]
            },
        )
        if mutation is None:
            assert require_if_delegated(
                config, policy, ReceiptStore(), db, job, "sample-automation", time.time()
            )
        else:
            with pytest.raises(BridgeError):
                require_if_delegated(
                    config, policy, ReceiptStore(), db, job, "sample-automation", time.time()
                )
