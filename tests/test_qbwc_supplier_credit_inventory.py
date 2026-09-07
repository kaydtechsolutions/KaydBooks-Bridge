"""Web Connector supplier returns prove stock and cost effects."""

# ruff: noqa: F401,F811
import pytest

from kaydbooks_bridge.qbwc_posting import enqueue
from test_bill_lookup import exact_case
from test_direct_sdk import direct
from test_inventory_supplier_credits import StockSession, inventory_case
from test_qbwc_discovery import authenticate, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service
from test_qbwc_supplier_credits import test_credit_effects_and_query_only_recovery as qualify
from test_supplier_credits import credit_case, queue_credit


@pytest.fixture
def queued_credit(inventory_case):
    return queue_credit(inventory_case, StockSession())


@pytest.mark.parametrize("lost", [False, True])
def test_return_stock_readback_and_recovery(queued_credit, lost):
    qualify(queued_credit, lost)
    bridge, token, job, simulator = queued_credit
    proof = bridge.status(token, "company-a", job)["transaction_receipt"]["receipt"]
    stock = proof["balance_effects"]["stock_effects"]["INV-A"]
    assert (stock["before"], stock["returned"], stock["after"]) == ("2", "2", "0")


@pytest.mark.parametrize("fault", ["stock"])
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
