"""Saved invoice readback completes a job without a dispatch or a duplicate write."""

import json
import sqlite3
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_direct_sdk import direct, transport  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case, saved_receipt  # noqa: F401
from test_qbwc_discovery import COMPANY_B, PASSWORD_A, discovery_setup  # noqa: F401


def receipt_exchange(mutate=None, company=None):
    def exchange(request, destination):
        req = ET.fromstring(request)
        query = req[0][-1]
        assert query.tag == "InvoiceQueryRq"
        assert query.findtext("TxnID") == "saved-id"
        assert query.findtext("IncludeLineItems") == "true"
        assert query.findtext("IncludeLinkedTxns") == "true"
        req[0].remove(query)
        send = transport(company) if company else transport()
        send(ET.tostring(req, encoding="unicode"), destination)
        result = ET.fromstring(destination.read_text())
        invoice = saved_receipt()[0][0]
        invoice.set("requestID", query.get("requestID"))
        if mutate:
            mutate(invoice)
        result[0].append(invoice)
        destination.write_text(ET.tostring(result, encoding="unicode"))

    return exchange


@pytest.fixture
def saved_job(receipt_case, commercial):  # noqa: F811
    policy, payload = receipt_case
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] += [
        "submit",
        "simulate",
    ]
    path.write_text(json.dumps(raw))
    svc = DurableQBWCDiscoveryService.from_path(path)

    def single(rows):
        rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
        for kind, key in (("Account", "ar-id"), ("Customer", "customer-id")):
            rows[(kind, key)].pop("CurrencyRef")

    discover(
        svc,
        token,
        "connector-company-a",
        PASSWORD_A,
        "981",
        invoice_check=payload,
        exchange=lambda rq, dest: dest.write_text(response(rq, taxable=False, mutate=single)),
    )
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        payload=payload,
        master_evidence={
            "transport": "direct-sdk",
            "connector": "connector-company-a",
            "id": "981",
        },
    )
    bridge = Bridge(path)
    job = bridge.prepare(token, "company-a", envelope)
    bridge.action(token, "company-a", job["id"], "validate")
    reference = {"transport": "direct-sdk", "connector": "connector-company-a", "id": "982"}
    return bridge, token, job["id"], envelope, reference


def read_saved(case, **kwargs):
    bridge, token, _, envelope, ref = case
    return discover(
        DurableQBWCDiscoveryService.from_path(bridge.config_path),
        token,
        ref["connector"],
        PASSWORD_A,
        ref["id"],
        receipt_check={"txn_id": "saved-id", "payload": envelope["payload"]},
        **kwargs,
    )


def test_receipt_survives_restart_and_prevents_duplicate_after_expiry(saved_job):
    bridge, token, job_id, envelope, ref = saved_job
    assert read_saved(saved_job, exchange=receipt_exchange())["receipt"]["txn_id"] == "saved-id"
    assert (
        read_saved(saved_job, exchange=lambda *_: pytest.fail("no repeat read"))["state"]
        == "verified"
    )
    job = bridge.attach_receipt(token, "company-a", job_id, ref)
    assert job["state"] == "verified" and job["txn_id"] == "saved-id"
    assert job["attempt"] is None and job["lease_until"] is None
    assert job["transaction_receipt"]["bridge_dispatched"] is False
    later = Bridge(bridge.config_path, clock=lambda: time.time() + 901)
    assert later.attach_receipt(token, "company-a", job_id, ref) == job
    envelope.pop("master_evidence")
    envelope["idempotency_key"] = "duplicate-new-key"
    assert later.prepare(token, "company-a", envelope) == job
    for action in ("validate", "submit"):
        with pytest.raises(BridgeError, match="current job state"):
            later.action(token, "company-a", job_id, action)
    with pytest.raises(BridgeError, match="validated"):
        later.preview(token, "company-a", job_id)
    assert later.audit(token, "company-a")["valid"]
    assert later.status(token, "company-a", job_id) == job


@pytest.mark.parametrize("fault", ["amount", "txn", "correlation", "company", "missing"])
def test_sdk_receipt_rejects_bad_response(saved_job, fault):
    def mutate(invoice):
        if fault == "amount":
            invoice.find("InvoiceRet/Subtotal").text = "11"
        elif fault == "txn":
            invoice.find("InvoiceRet/TxnID").text = "other"
        elif fault == "correlation":
            invoice.set("requestID", "1")
        elif fault == "missing":
            invoice.remove(invoice[0])

    with pytest.raises(BridgeError, match="validation failed"):
        read_saved(
            saved_job, exchange=receipt_exchange(mutate, COMPANY_B if fault == "company" else None)
        )
    bridge, token, job_id, _, ref = saved_job
    with pytest.raises(BridgeError, match="verified SDK"):
        bridge.attach_receipt(token, "company-a", job_id, ref)
    assert bridge.status(token, "company-a", job_id)["state"] == "validated"


@pytest.mark.parametrize(
    "fault",
    [
        "stale",
        "future",
        "permission",
        "owner",
        "policy",
        "binding",
        "reference",
        "unknown",
        "master-run",
    ],
)
def test_attachment_failures_leave_job_unchanged(saved_job, fault, monkeypatch):
    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    raw = json.loads(Path(bridge.config_path).read_text())
    actor = next(iter(raw["principals"]))
    if fault == "stale":
        bridge = Bridge(bridge.config_path, clock=lambda: time.time() + 901)
    elif fault == "future":
        bridge = Bridge(bridge.config_path, clock=lambda: 0)
    elif fault == "permission":
        raw["principals"][actor]["companies"]["company-a"].remove("recover")
    elif fault == "owner":
        raw["principals"]["other-actor"] = dict(
            raw["principals"][actor], token_env="KAYDBOOKS_OTHER_TOKEN"
        )
        token = "other-" + "x" * 40
        monkeypatch.setenv("KAYDBOOKS_OTHER_TOKEN", token)
    elif fault == "policy":
        raw["companies"]["company-a"]["account_roles"]["invoice_receivable"] = "changed-id"
    elif fault == "binding":
        raw["connectors"][ref["connector"]]["identity_sha256"] = "a" * 64
    elif fault == "reference":
        ref["receipt"] = {"txn_id": "saved-id"}
    elif fault == "unknown":
        ref["id"] = "999"
    elif fault == "master-run":
        ref["id"] = "981"
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        bridge.attach_receipt(token, "company-a", job_id, ref)
    config = Config.load(bridge.config_path)
    store = Store(config.root, "company-a")
    with store.transaction() as db:
        assert store.job(db, job_id)["state"] == "validated"
        assert db.execute("SELECT COUNT(*) FROM invoice_receipts").fetchone()[0] == 0


def test_attachment_atomic_and_immutable(saved_job, monkeypatch):
    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    original = Store.event

    def fail(self, db, at, actor, job, event, data):
        if event == "invoice_receipt_attached":
            raise RuntimeError("interrupted transaction")
        return original(self, db, at, actor, job, event, data)

    with monkeypatch.context() as patch:
        patch.setattr(Store, "event", fail)
        with pytest.raises(RuntimeError):
            bridge.attach_receipt(token, "company-a", job_id, ref)
    assert bridge.status(token, "company-a", job_id)["state"] == "validated"
    bridge.attach_receipt(token, "company-a", job_id, ref)
    store = Store(Config.load(bridge.config_path).root, "company-a")
    with store.transaction() as db:
        for sql in (
            "DELETE FROM invoice_receipts",
            "UPDATE invoice_receipts SET evidence='{}'",
            "UPDATE jobs SET state='draft'",
            "UPDATE jobs SET txn_id='other'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    with pytest.raises(BridgeError, match="cannot be replaced"):
        bridge.attach_receipt(token, "company-a", job_id, dict(ref, id="999"))


def test_unproven_state_change_and_reused_run_are_rejected(saved_job):
    bridge, token, job_id, envelope, ref = saved_job
    store = Store(Config.load(bridge.config_path).root, "company-a")
    with store.transaction() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE jobs SET state='verified',txn_id='fake' WHERE id=?", (job_id,))
    read_saved(saved_job, exchange=receipt_exchange())
    envelope["payload"]["ref_number"] = "changed"
    with pytest.raises(BridgeError, match="ownership"):
        read_saved(saved_job, exchange=lambda *_: pytest.fail("must not dispatch changed run"))


def test_sdk_recovers_saved_read_without_redelivery(saved_job):
    def crash(request, destination):
        receipt_exchange()(request, destination)
        raise RuntimeError("parent stopped")

    with pytest.raises(RuntimeError):
        read_saved(saved_job, exchange=crash)
    assert (
        read_saved(saved_job, exchange=lambda *_: pytest.fail("read repeated"))["state"]
        == "verified"
    )


@pytest.mark.parametrize("permission", ["read", "validate", "recover"])
def test_replay_rechecks_permissions(saved_job, permission):
    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    bridge.attach_receipt(token, "company-a", job_id, ref)
    raw = json.loads(Path(bridge.config_path).read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(permission)
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        bridge.attach_receipt(token, "company-a", job_id, ref)


def test_late_sdk_replay_does_not_refresh_receipt_age(saved_job, monkeypatch):
    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    now = time.time() + 901
    monkeypatch.setattr("kaydbooks_bridge.direct_sdk.time.time", lambda: now)
    read_saved(saved_job, exchange=lambda *_: pytest.fail("read repeated"))
    with pytest.raises(BridgeError, match="stale"):
        Bridge(bridge.config_path, clock=lambda: now).attach_receipt(
            token, "company-a", job_id, ref
        )


def test_maximum_sdk_correlation_is_supported(saved_job):
    saved_job[-1]["id"] = "1234567890123456"
    assert read_saved(saved_job, exchange=receipt_exchange())["state"] == "verified"


def test_queued_job_can_reconcile_without_dispatch(saved_job):
    bridge, token, job_id, _, ref = saved_job
    raw = json.loads(Path(bridge.config_path).read_text())
    raw["companies"]["company-a"]["approval_required"] = False
    Path(bridge.config_path).write_text(json.dumps(raw))
    bridge.action(token, "company-a", job_id, "submit")
    read_saved(saved_job, exchange=receipt_exchange())
    assert bridge.attach_receipt(token, "company-a", job_id, ref)["attempt"] is None


def test_original_database_migrates_transition_guard(saved_job):
    bridge, token, job_id, _, ref = saved_job
    store = Store(Config.load(bridge.config_path).root, "company-a")
    with store.transaction() as db:
        db.execute("DROP TRIGGER external_receipt_terminal_guard")
        db.execute("DROP TABLE invoice_receipts")
        db.execute("DROP TRIGGER jobs_state_transition_guard")
        db.execute(
            "CREATE TRIGGER jobs_state_transition_guard BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT,'old guard'); END"
        )
    read_saved(saved_job, exchange=receipt_exchange())
    assert bridge.attach_receipt(token, "company-a", job_id, ref)["state"] == "verified"


def test_cli_receipt_attachment_and_status(saved_job, tmp_path, monkeypatch, capsys):
    from kaydbooks_bridge.cli import main

    bridge, token, job_id, _, ref = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    reference_file = tmp_path / "reference.json"
    reference_file.write_text(json.dumps(ref))
    monkeypatch.setenv("KAYDBOOKS_TOKEN", token)
    common = ["--config", str(bridge.config_path), "--company", "company-a"]
    assert main([*common, "attach-receipt", job_id, str(reference_file)]) == 0
    job = json.loads(capsys.readouterr().out)
    assert job["txn_id"] == "saved-id" and job["transaction_receipt"]["bridge_dispatched"] is False
    assert main([*common, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["txn_id"] == "saved-id"
