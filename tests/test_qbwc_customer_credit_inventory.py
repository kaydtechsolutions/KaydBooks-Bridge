"""Web Connector customer returns prove stock and cost effects."""

# ruff: noqa: F401,F811
import pytest

from kaydbooks_bridge.qbwc_posting import enqueue
from test_customer_credits import credit_case, queued_credit
from test_direct_sdk import direct
from test_inventory_customer_credits import stock_transport
from test_inventory_sales import receipt_case
from test_invoice_commercial import commercial
from test_invoice_compatibility import setup_invoice
from test_qbwc_customer_credits import test_credit_effects_and_query_only_recovery as qualify
from test_qbwc_discovery import authenticate, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service


@pytest.mark.parametrize("lost", [False, True])
def test_return_stock_readback_and_recovery(queued_credit, lost):
    qualify(queued_credit, lost)
    bridge, token, job, simulator = queued_credit
    proof = bridge.status(token, "company-a", job)["transaction_receipt"]["receipt"]
    stock = proof["balance_effects"]["stock_effects"]["service-id"]
    assert (stock["before"], stock["returned"], stock["after"]) == ("0", "2", "2")
    assert stock["average_cost_before"] == stock["average_cost_after"] == "5.00"


@pytest.mark.parametrize("fault", ["stock", "cost"])
def test_return_wrong_stock_or_cost_is_held(queued_credit, fault):
    bridge, token, job, simulator = queued_credit
    setattr(simulator, "wrong_" + fault, True)
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    preflight = send(svc, ticket)
    assert receive(svc, ticket, simulator.xml(preflight)) == 25
    answer = simulator(preflight, send(svc, ticket), None, lambda _: True)
    assert receive(svc, ticket, answer) == 75
    assert receive(svc, ticket, simulator.xml(send(svc, ticket))) == -1
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"
    assert simulator.writes == 1
