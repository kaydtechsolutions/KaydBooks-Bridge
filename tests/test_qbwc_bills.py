"""Supplier bill callbacks, retained evidence and recovery without native fallback."""
# ruff: noqa: F401,F811

import json
import sqlite3
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.bill_evidence import resolve
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.web_ui import check_masters, manual
from test_bill_lookup import exact_case, exact_response
from test_direct_sdk import direct
from test_inventory_bills import StockSession, inventory_case, stock_bill, stock_read
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service
from test_sample_bills import Session, queue_case, queued_bill, receipt_exchange, saved_bill


def preflight(svc, ticket, *, existing=False):
    request = send(svc, ticket)
    assert "BillQueryRq" in request and "BillAddRq" not in request
    answers = []
    Session(existing=existing)(request, None, None, lambda xml: answers.append(xml))
    return receive(svc, ticket, answers[0])


@pytest.mark.parametrize("lost", [False, True])
def test_bill_posting_and_lost_response_recovery(queued_bill, tmp_path, lost):
    bridge, token, job, _ = queued_bill
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert preflight(svc, ticket) == 25
    request = send(service(bridge), ticket)
    assert "BillAddRq" in request and "InvoiceAddRq" not in request
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        bridge.pause(token, "company-a", True)
        recover(bridge, token, "company-a", job)
        svc = service(bridge)
        ticket, _ = authenticate(svc)
        assert preflight(svc, ticket, existing=True) == 75
    else:
        answer = ET.tostring(
            saved_bill(ET.fromstring(request)[0][0].get("requestID"), operation="BillAdd"),
            encoding="unicode",
        )
        assert receive(svc, ticket, answer) == 75
        assert receive(svc, ticket, answer) == 75
    request = send(service(bridge), ticket)
    assert "BillToPayQueryRq" in request and "BillAddRq" not in request
    dest = tmp_path / "lookup.xml"
    receipt_exchange(request, dest)
    assert receive(svc, ticket, dest.read_text()) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified" and result["posting_transport"] == "qbwc"
    assert result["transaction_receipt"]["receipt"]["balance_verification"] == "matched-bill-to-pay"
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "bill.create") == 1
        assert attempt_count(db, "invoice.create") == 0
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 1
        )
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)
    with pytest.raises(BridgeError):
        recover(bridge, token, "company-a", job)
    assert bridge.audit(token, "company-a")["valid"]


def test_bill_browser_check_and_owned_fresh_evidence(exact_case, monkeypatch):
    path, token, payload = exact_case
    bridge = Bridge(path)
    monkeypatch.setattr(
        "kaydbooks_bridge.web_ui.discover", lambda *a, **k: pytest.fail("native fallback")
    )
    args = (bridge, token, "company-a", "bill.create", "connector-company-a", payload)
    pending = check_masters(*args)
    assert pending["pending"] and pending["evidence"] is None
    assert check_masters(*args)["result"]["job"] == pending["result"]["job"]
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)
    assert "VendorQueryRq" in request and "BillAddRq" not in request
    assert receive(svc, ticket, exact_response(request)) == 100
    call(svc, "closeConnection", ticket=ticket)
    check = check_masters(*args)
    assert not check["pending"] and check["evidence"]["transport"] == "qbwc"
    cfg, actor, policy, store = bridge._context(token, "company-a", "read")
    with store.transaction() as db:
        proof = resolve(cfg, policy, store, db, actor, payload, check["evidence"], bridge.clock())
        assert proof["identity_sha256"]
        with pytest.raises(BridgeError, match="stale"):
            resolve(
                cfg, policy, store, db, actor, payload, check["evidence"], bridge.clock() + 90000
            )
        with pytest.raises(BridgeError, match="owned"):
            resolve(
                cfg,
                policy,
                store,
                db,
                actor,
                {**payload, "ref_number": "OTHER"},
                check["evidence"],
                bridge.clock(),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), store.transaction() as db:
        db.execute("UPDATE qbwc_invoice_jobs SET operation='invoice.create'")
    job = manual(
        bridge,
        token,
        "company-a",
        "bill-qbwc",
        cfg.companies["company-a"].sources[0],
        "bill.create",
        payload,
        check["evidence"],
    )
    assert bridge.action(token, "company-a", job["id"], "validate")["state"] == "validated"


def test_bill_recovery_missing_result_never_resends(queued_bill):
    bridge, token, job, _ = queued_bill
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert preflight(svc, ticket) == 25
    assert "BillAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(bridge, token, "company-a", job)
    ticket, _ = authenticate(svc)
    assert preflight(svc, ticket) == -1
    assert send(svc, ticket) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"


def test_bill_never_started_attempt_preserves_quota(queued_bill):
    bridge, token, job, _ = queued_bill
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    call(svc, "closeConnection", ticket=ticket)
    assert recover(bridge, token, "company-a", job)["detail"] == "qbwc_not_dispatched"
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "bill.create") == 1


@pytest.mark.parametrize("quantity", ["1", "2"])
def test_bill_qbwc_inventory_requires_exact_stock_increase(inventory_case, tmp_path, quantity):
    bridge, token, job, _ = queue_case(inventory_case)
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    responses = []
    StockSession()(send(svc, ticket), None, None, lambda xml: responses.append(xml))
    assert receive(svc, ticket, responses[0]) == 25
    request = send(svc, ticket)
    answer = stock_bill(
        saved_bill(ET.fromstring(request)[0][0].get("requestID"), operation="BillAdd")
    )
    assert receive(svc, ticket, ET.tostring(answer, encoding="unicode")) == 75
    destination = tmp_path / "stock.xml"
    stock_read(send(svc, ticket), destination, quantity)
    assert receive(svc, ticket, destination.read_text()) == (100 if quantity == "2" else -1)
    result = bridge.status(token, "company-a", job)
    if quantity == "2":
        assert result["transaction_receipt"]["receipt"]["stock_effects"]["INV-A"]["received"] == "2"
    else:
        assert result["state"] == "posted-unverified"


def test_bill_read_rejects_changed_authority_before_callback(exact_case):
    path, token, payload = exact_case
    bridge = Bridge(path)
    check_masters(bridge, token, "company-a", "bill.create", "connector-company-a", payload)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    raw = json.loads(path.read_text())
    principal = next(iter(raw["principals"]))
    raw["principals"][principal]["companies"]["company-a"].remove("validate")
    path.write_text(json.dumps(raw))
    assert send(service(bridge), ticket) == ""
