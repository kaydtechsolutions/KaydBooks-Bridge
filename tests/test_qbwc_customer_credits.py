"""Credit memo QBWC evidence, exact allocation effects and no-write recovery."""
# ruff: noqa: F401,F811

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.credit_evidence import resolve
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.web_ui import check_masters, manual
from test_customer_credits import credit_case, queued_credit
from test_direct_sdk import direct
from test_invoice_commercial import commercial
from test_invoice_compatibility import setup_invoice
from test_invoice_receipt import receipt_case
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service


@pytest.mark.parametrize("lost", [False, True])
def test_credit_effects_and_query_only_recovery(queued_credit, lost):
    bridge, token, job, simulator = queued_credit
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    preflight = send(svc, ticket)
    assert "CreditMemoAddRq" not in preflight
    assert receive(svc, ticket, simulator.xml(preflight)) == 25
    write = send(service(bridge), ticket)
    assert "CreditMemoAddRq" in write
    answer = simulator(preflight, write, None, lambda _: True)
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        bridge.pause(token, "company-a", True)
        recover(bridge, token, "company-a", job)
        svc = service(bridge)
        ticket, _ = authenticate(svc)
        query = send(svc, ticket)
        assert "CreditMemoAddRq" not in query
        assert receive(svc, ticket, simulator.xml(query)) == 75
    else:
        assert receive(svc, ticket, answer) == 75
    lookup = send(service(bridge), ticket)
    assert "CreditMemoAddRq" not in lookup
    assert receive(svc, ticket, simulator.xml(lookup)) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified" and result["posting_transport"] == "qbwc"
    proof = result["transaction_receipt"]["receipt"]
    assert proof["txn_id"] == "credit-id"
    assert proof["balance_effects"]["before"] == "25.00"
    assert proof["balance_effects"]["after"] == "15.00"
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "customer-credit.create") == 1
        assert attempt_count(db, "invoice.create") == 0
        assert attempt_count(db, "bill.create") == 0
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 1
        )
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)
    assert simulator.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_payment_mismatched_balance_stays_unverified(queued_credit):
    bridge, token, job, simulator = queued_credit
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    preflight = send(svc, ticket)
    assert receive(svc, ticket, simulator.xml(preflight)) == 25
    write = send(svc, ticket)
    assert receive(svc, ticket, simulator(preflight, write, None, lambda _: True)) == 75
    # Keep the queried invoice internally consistent, but with the wrong reduction.
    from decimal import Decimal

    simulator.balance = Decimal("24.00")
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == -1
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"


def test_payment_browser_check_uses_owned_fresh_qbwc_evidence(queued_credit, monkeypatch):
    bridge, token, job, simulator = queued_credit
    payload = {
        **bridge.status(token, "company-a", job)["payload"],
        "ref_number": bridge.status(token, "company-a", job)["payload"]["ref_number"] + "2",
    }
    monkeypatch.setattr(
        "kaydbooks_bridge.web_ui.discover", lambda *a, **k: pytest.fail("native fallback")
    )
    args = (bridge, token, "company-a", "customer-credit.create", "connector-company-a", payload)
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
        assert proof["balances"]["customer_balance"] == "25.00"
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
        "customer-credit.create",
        payload,
        checked["evidence"],
    )
    assert bridge.action(token, "company-a", prepared["id"], "validate")["state"] == "validated"


def test_payment_missing_recovery_result_cannot_resend(queued_credit):
    bridge, token, job, simulator = queued_credit
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == 25
    assert "CreditMemoAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(bridge, token, "company-a", job)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == -1
    assert send(svc, ticket) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)


def test_payment_revoked_permission_prevents_write(queued_credit):
    bridge, token, job, simulator = queued_credit
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


def test_payment_never_started_preserves_attempt(queued_credit):
    bridge, token, job, _ = queued_credit
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    call(svc, "closeConnection", ticket=ticket)
    assert recover(bridge, token, "company-a", job)["detail"] == "qbwc_not_dispatched"
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "customer-credit.create") == 1
