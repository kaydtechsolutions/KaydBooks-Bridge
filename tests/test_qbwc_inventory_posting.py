"""Inventory callbacks reuse native stock validators through QBWC-only exchanges."""
# ruff: noqa: F401,F811

import pytest

from kaydbooks_bridge.qbwc_posting import recover
from test_direct_sdk import direct
from test_inventory_sales import StockSession, receipt_case, stock_read
from test_invoice_commercial import commercial
from test_invoice_compatibility import setup_invoice
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service, start, write_response
from test_receipt_lifecycle import saved_job
from test_sample_posting import queued


@pytest.mark.parametrize("lost", [False, True])
@pytest.mark.parametrize("quantity", ["0", "1"])
def test_qbwc_inventory_stock_and_interrupted_write(queued, tmp_path, lost, quantity):
    bridge, token, job, svc, ticket = start(queued)
    captured = []
    StockSession()(send(svc, ticket), None, None, lambda xml: captured.append(xml))
    assert receive(svc, ticket, captured[0]) == 25
    request = send(svc, ticket)
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        recover(bridge, token, "company-a", job)
        svc = service(bridge)
        ticket, _ = authenticate(svc)
        captured = []
        StockSession(existing=True)(send(svc, ticket), None, None, lambda xml: captured.append(xml))
        assert receive(svc, ticket, captured[0]) == 75
    else:
        assert receive(svc, ticket, write_response(request)) == 75
    dest = tmp_path / "stock.xml"
    stock_read(send(svc, ticket), dest, quantity)
    assert receive(svc, ticket, dest.read_text()) == (100 if quantity == "0" else -1)
    saved = bridge.status(token, "company-a", job)
    if quantity == "0":
        assert saved["state"] == "verified"
        assert saved["transaction_receipt"]["receipt"]["stock_effects"]["service-id"]["sold"] == "2"
    else:
        assert saved["state"] in ("unknown", "posted-unverified")
