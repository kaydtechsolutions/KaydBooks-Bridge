"""QBWC saved-invoice checks share receipt validation and durable lifecycle controls."""
# ruff: noqa: F811

import json
import sqlite3
import time
from pathlib import Path

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.qbwc_accounts import account_job
from kaydbooks_bridge.qbwc_invoices import invoice_job
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case  # noqa: F401
from test_qbwc_discovery import authenticate, call, discovery_setup, receive  # noqa: F401
from test_qbwc_invoices import send
from test_receipt_lifecycle import read_saved, receipt_exchange, saved_job  # noqa: F401


def queue(case, job_id="receipt-one"):
    bridge, token, _, envelope, _ = case
    svc = DurableQBWCDiscoveryService.from_path(bridge.config_path)
    assert (
        invoice_job(
            svc,
            token,
            "connector-company-a",
            job_id,
            payload=envelope["payload"],
            enqueue=True,
            txn_id="saved-id",
        )["state"]
        == "queued"
    )
    return svc


def complete(case, tmp_path, job_id="receipt-one", mutate=None):
    svc = queue(case, job_id)
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)
    restarted = DurableQBWCDiscoveryService.from_path(case[0].config_path)
    assert send(restarted, ticket) == request
    dest = tmp_path / "receipt.xml"
    receipt_exchange(mutate)(request, dest)
    result = receive(restarted, ticket, dest.read_text())
    assert receive(restarted, ticket, dest.read_text()) == result
    call(restarted, "closeConnection", ticket=ticket)
    return restarted, {"transport": "qbwc", "connector": "connector-company-a", "id": job_id}


def test_qbwc_attachment_restart_and_one_update(saved_job, tmp_path):
    svc, ref = complete(saved_job, tmp_path)
    bridge, token, job_id, envelope, _ = saved_job
    result = invoice_job(svc, token, ref["connector"], ref["id"])
    assert result["receipt"]["txn_id"] == "saved-id" and result["transport"] == "qbwc"
    job = bridge.attach_receipt(token, "company-a", job_id, ref)
    assert job["state"] == "verified" and job["attempt"] is None
    assert job["transaction_receipt"]["reference"] == ref
    assert Bridge(bridge.config_path).attach_receipt(token, "company-a", job_id, ref) == job
    envelope.pop("master_evidence")
    assert (
        Bridge(bridge.config_path, clock=lambda: time.time() + 901).prepare(
            token, "company-a", envelope
        )
        == job
    )
    ticket, _ = authenticate(svc)
    assert "InvoiceQueryRq" not in send(svc, ticket)


def test_qbwc_fresh_confirmation_preserves_sdk_receipt(saved_job, tmp_path):
    bridge, token, job_id, _, sdkref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    original = bridge.attach_receipt(token, "company-a", job_id, sdkref)
    _, ref = complete(saved_job, tmp_path)
    observation = bridge.verify_receipt(token, "company-a", job_id, ref)
    assert observation["observation"]["reference"] == ref
    assert bridge.status(token, "company-a", job_id) == original
    assert any(
        event["event"] == "invoice_receipt_confirmed"
        for event in bridge.audit(token, "company-a")["events"]
    )
    with pytest.raises(BridgeError, match="stale"):
        Bridge(bridge.config_path, clock=lambda: time.time() + 901).verify_receipt(
            token, "company-a", job_id, ref
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("TxnID", "wrong"),
        ("Subtotal", "11"),
        ("IsPaid", "true"),
        ("InvoiceLineRet/ItemRef/ListID", "other"),
        ("SalesTaxTotal", "1"),
    ],
)
def test_bad_saved_record_never_attaches(saved_job, tmp_path, field, value):
    svc, ref = complete(
        saved_job,
        tmp_path,
        mutate=lambda invoice: setattr(invoice.find("InvoiceRet/" + field), "text", value),
    )
    bridge, token, job_id, _, _ = saved_job
    assert "receipt" not in invoice_job(svc, token, ref["connector"], ref["id"])
    with pytest.raises(BridgeError, match="verified QBWC"):
        bridge.attach_receipt(token, "company-a", job_id, ref)


@pytest.mark.parametrize("stage", ["before-send", "after-send", "after-response"])
@pytest.mark.parametrize("change", ["permission", "policy"])
def test_callbacks_recheck_receipt_context(saved_job, tmp_path, stage, change):
    svc = queue(saved_job)
    ticket, _ = authenticate(svc)
    if stage != "before-send":
        request = send(svc, ticket)
        dest = tmp_path / "receipt.xml"
        receipt_exchange()(request, dest)
        response = dest.read_text()
        if stage == "after-response":
            assert receive(svc, ticket, response) == 100
    path = Path(saved_job[0].config_path)
    raw = json.loads(path.read_text())
    if change == "permission":
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "validate"
        )
    else:
        raw["companies"]["company-a"]["account_roles"]["invoice_receivable"] = "other"
    path.write_text(json.dumps(raw))
    svc = DurableQBWCDiscoveryService.from_path(path)
    if stage == "before-send":
        assert send(svc, ticket) == ""
    else:
        assert receive(svc, ticket, response) == -1


def test_selector_queue_and_session_assignment_are_immutable(saved_job):
    svc = queue(saved_job)
    token, payload = saved_job[1], saved_job[3]["payload"]
    with pytest.raises(BridgeError, match="immutable"):
        invoice_job(
            svc,
            token,
            "connector-company-a",
            "receipt-one",
            txn_id="other",
            enqueue=True,
            payload=payload,
        )
    with pytest.raises(BridgeError, match="queued"):
        account_job(svc, token, "connector-company-a", "account-one", enqueue=True)
    with pytest.raises(BridgeError, match="queued"):
        invoice_job(svc, token, "connector-company-a", "master-one", enqueue=True, payload=payload)
    ticket, _ = authenticate(svc)
    with svc._stores["company-a"].transaction() as db:
        for sql in (
            "UPDATE qbwc_invoice_jobs SET txn_id=NULL",
            "UPDATE qbwc_invoice_jobs SET ticket=NULL",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert (
        call(svc, "connectionError", ticket=ticket, hresult="0x80040408", message="synthetic")
        == "done"
    )
    with pytest.raises(BridgeError, match="verified QBWC"):
        saved_job[0].attach_receipt(
            token,
            "company-a",
            saved_job[2],
            {"transport": "qbwc", "connector": "connector-company-a", "id": "receipt-one"},
        )


def test_sdk_receipt_upgrade_preserves_history(saved_job):
    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    original = bridge.attach_receipt(token, "company-a", job_id, ref)
    store = Store(Config.load(bridge.config_path).root, "company-a")
    with store.transaction() as db:
        for name in (
            "receipt_no_update",
            "receipt_no_delete",
            "receipt_insert_guard",
            "jobs_state_transition_guard",
            "external_receipt_terminal_guard",
        ):
            db.execute(f"DROP TRIGGER {name}")
        db.execute(
            "CREATE TABLE old_receipts (job_id TEXT PRIMARY KEY REFERENCES jobs(id),txn_id TEXT NOT NULL UNIQUE,actor TEXT NOT NULL,connector TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES sdk_discovery(id),context_hash TEXT NOT NULL,evidence TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO old_receipts SELECT job_id,txn_id,actor,connector,run_id,context_hash,evidence FROM invoice_receipts"
        )
        db.execute("DROP TABLE invoice_receipts")
        db.execute("ALTER TABLE old_receipts RENAME TO invoice_receipts")
    assert Bridge(bridge.config_path).status(token, "company-a", job_id) == original
    with Store(Config.load(bridge.config_path).root, "company-a").transaction() as db:
        assert db.execute("SELECT transport FROM invoice_receipts").fetchone()[0] == "direct-sdk"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM invoice_receipts")


def test_qbwc_attachment_rejects_stale_or_wrong_transport(saved_job, tmp_path):
    _, ref = complete(saved_job, tmp_path)
    bridge, token, job_id, _, _ = saved_job
    with pytest.raises(BridgeError, match="stale"):
        Bridge(bridge.config_path, clock=lambda: time.time() + 901).attach_receipt(
            token, "company-a", job_id, ref
        )
    with pytest.raises(BridgeError):
        bridge.attach_receipt(token, "company-a", job_id, dict(ref, transport="direct-sdk"))


def test_cli_fresh_qbwc_verification(saved_job, tmp_path, monkeypatch, capsys):
    from kaydbooks_bridge.cli import main

    bridge, token, job_id, _, sdkref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    bridge.attach_receipt(token, "company-a", job_id, sdkref)
    _, ref = complete(saved_job, tmp_path)
    file = tmp_path / "reference.json"
    file.write_text(json.dumps(ref))
    monkeypatch.setenv("KAYDBOOKS_TOKEN", token)
    assert (
        main(
            [
                "--config",
                str(bridge.config_path),
                "--company",
                "company-a",
                "verify-receipt",
                job_id,
                str(file),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["observation"]["reference"] == ref


@pytest.mark.parametrize("permission", ["read", "validate"])
def test_fresh_confirmation_checks_current_grants(saved_job, tmp_path, permission):
    bridge, token, job_id, _, sdkref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    bridge.attach_receipt(token, "company-a", job_id, sdkref)
    _, ref = complete(saved_job, tmp_path)
    raw = json.loads(Path(bridge.config_path).read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(permission)
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        bridge.verify_receipt(token, "company-a", job_id, ref)
