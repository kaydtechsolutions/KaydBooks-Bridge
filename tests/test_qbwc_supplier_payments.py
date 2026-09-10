"""Supplier payment QBWC evidence, exact allocation effects and no-write recovery."""
# ruff: noqa: F401,F811

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.supplier_payment_evidence import resolve
from kaydbooks_bridge.web_ui import check_masters, manual
from test_direct_sdk import direct
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service
from test_sample_supplier_payments import Session, queued_payment
from test_supplier_payments import payment_case, response


@pytest.mark.parametrize("lost", [False, True])
@pytest.mark.parametrize("kind", ["partial", "full", "discount"])
def test_payment_effects_and_query_only_recovery(queued_payment, lost, kind):
    bridge, token, job, simulator = queued_payment(
        "10.00" if kind == "full" else "5.00",
        discount="1.00" if kind == "discount" else None,
    )
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    preflight = send(svc, ticket)
    assert "BillPaymentCheckAddRq" not in preflight
    assert receive(svc, ticket, simulator.xml(preflight)) == 25
    write = send(service(bridge), ticket)
    assert "BillPaymentCheckAddRq" in write
    answer = simulator(preflight, write, None, lambda _: True)
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        bridge.pause(token, "company-a", True)
        recover(bridge, token, "company-a", job)
        svc = service(bridge)
        ticket, _ = authenticate(svc)
        query = send(svc, ticket)
        assert "BillPaymentCheckAddRq" not in query
        assert receive(svc, ticket, simulator.xml(query)) == 75
    else:
        assert receive(svc, ticket, answer) == 75
    lookup = send(service(bridge), ticket)
    assert "BillPaymentCheckAddRq" not in lookup
    assert receive(svc, ticket, simulator.xml(lookup)) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified" and result["posting_transport"] == "qbwc"
    proof = result["transaction_receipt"]["receipt"]
    assert proof["txn_id"] == "payment-id"
    assert proof["balance_effects"] == (
        {
            "bill-id": {
                "before": "10.00",
                "payment": "10.00" if kind == "full" else "5.00",
                **({"discount": "1.00"} if kind == "discount" else {}),
                "after": "0.00" if kind == "full" else "4.00" if kind == "discount" else "5.00",
            }
        }
    )
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "supplier-payment.create") == 1
        assert attempt_count(db, "invoice.create") == 0
        assert attempt_count(db, "bill.create") == 0
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 1
        )
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)
    assert simulator.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_payment_mismatched_balance_stays_unverified(queued_payment):
    bridge, token, job, simulator = queued_payment()
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    preflight = send(svc, ticket)
    assert receive(svc, ticket, simulator.xml(preflight)) == 25
    write = send(svc, ticket)
    assert receive(svc, ticket, simulator(preflight, write, None, lambda _: True)) == 75
    # Keep the queried payable internally consistent, but with the wrong reduction.
    simulator.rows["BillToPay"][0]["BillToPay"]["AmountDue"] = "6.00"
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == -1
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"


def test_payment_browser_check_uses_owned_fresh_qbwc_evidence(queued_payment, monkeypatch):
    bridge, token, job, simulator = queued_payment()
    payload = {**bridge.status(token, "company-a", job)["payload"], "ref_number": "SYN-PAY-2"}
    monkeypatch.setattr(
        "kaydbooks_bridge.web_ui.discover", lambda *a, **k: pytest.fail("native fallback")
    )
    args = (bridge, token, "company-a", "supplier-payment.create", "connector-company-a", payload)
    pending = check_masters(*args)
    assert pending["pending"] and pending["evidence"] is None
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    checked = check_masters(*args)
    assert checked["evidence"]["transport"] == "qbwc" and not checked["pending"]
    config, actor, policy, store = bridge._context(token, "company-a", "read")
    with store.transaction() as db:
        proof = resolve(
            config, policy, store, db, actor, payload, checked["evidence"], bridge.clock()
        )
        assert proof["balances"]["bill-id"]["balance"] == "10.00"
        from kaydbooks_bridge.payment_evidence import resolve as customer_resolve

        with pytest.raises(BridgeError, match="owned"):
            customer_resolve(
                config, policy, store, db, actor, payload, checked["evidence"], bridge.clock()
            )
        with pytest.raises(BridgeError, match="stale"):
            resolve(
                config, policy, store, db, actor, payload, checked["evidence"], bridge.clock() + 901
            )
        with pytest.raises(BridgeError, match="owned"):
            resolve(
                config,
                policy,
                store,
                db,
                actor,
                {**payload, "ref_number": "OTHER"},
                checked["evidence"],
                bridge.clock(),
            )
    prepared = manual(
        bridge,
        token,
        "company-a",
        "qbwc-payment",
        policy.sources[0],
        "supplier-payment.create",
        payload,
        checked["evidence"],
    )
    assert bridge.action(token, "company-a", prepared["id"], "validate")["state"] == "validated"


def test_payment_missing_recovery_result_cannot_resend(queued_payment):
    bridge, token, job, simulator = queued_payment()
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == 25
    assert "BillPaymentCheckAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(bridge, token, "company-a", job)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == -1
    assert send(svc, ticket) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)


def test_payment_revoked_permission_prevents_write(queued_payment):
    bridge, token, job, simulator = queued_payment()
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == 25
    raw = json.loads(bridge.config_path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("post-sample")
    bridge.config_path.write_text(json.dumps(raw))
    assert send(svc, ticket) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"
    with svc._stores["company-a"].transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 0
        )


def test_payment_never_started_preserves_attempt(queued_payment):
    bridge, token, job, _ = queued_payment()
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    call(svc, "closeConnection", ticket=ticket)
    assert recover(bridge, token, "company-a", job)["detail"] == "qbwc_not_dispatched"
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "supplier-payment.create") == 1
