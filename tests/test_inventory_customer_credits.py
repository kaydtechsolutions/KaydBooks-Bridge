"""Customer stock returns require the original sale and independent stock/cost effects."""
# ruff: noqa: F811

import copy
import os
from xml.etree import ElementTree as E

import pytest

import test_customer_credits as original
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.customer_credits import (
    append_check,
    plan,
    validate_check,
    verify_balance_effect,
)
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_credit_posting import post, reconcile
from test_customer_credits import credit_case, queued_credit  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_inventory_sales import receipt_case  # noqa: F401
from test_invoice_commercial import commercial  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401


class StockSession(original.Session):
    def __init__(self):
        super().__init__()
        self.wrong_stock = self.wrong_cost = False
        self.mutate = self.observe

    def observe(self, root):
        row = root.find(".//ItemInventoryRet")
        row.find("QuantityOnHand").text = str(0 if self.wrong_stock else 2 * len(self.credits))
        row.find("QuantityOnSalesOrder").text = "0"
        row.find("AverageCost").text = "6.00" if self.wrong_cost and self.credits else "5.00"


@pytest.fixture(autouse=True)
def stock_transport(monkeypatch):
    monkeypatch.setattr(original, "Session", StockSession)


@pytest.mark.parametrize("crash", [False, True])
def test_sold_out_customer_return_increases_stock_without_resend(queued_credit, crash):
    bridge, token, job_id, session = queued_credit
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        result = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    else:
        result = post(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    assert result["state"] == "verified" and session.writes == 1
    stock = result["transaction_receipt"]["receipt"]["balance_effects"]["stock_effects"][
        "service-id"
    ]
    assert (stock["before"], stock["returned"], stock["after"]) == ("0", "2", "2")
    assert stock["average_cost_before"] == stock["average_cost_after"] == "5.00"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


@pytest.mark.parametrize("fault", ["stock", "cost"])
def test_incorrect_stock_or_cost_is_held(queued_credit, fault):
    bridge, token, job_id, session = queued_credit
    setattr(session, "wrong_" + fault, True)
    with pytest.raises(BridgeError, match="stock or average cost effect differs"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


@pytest.mark.parametrize(
    "fault", ["fifo", "lot", "negative", "zero-cost", "missing-cost", "source-quantity"]
)
def test_customer_return_checks_settings_cost_and_original_capacity(credit_case, fault):
    path, _, payload = credit_case
    check = plan(Config.load(path).companies["company-a"], payload)
    root = E.fromstring(
        StockSession().xml(append_check(S._discovery_request("1234", "17.0"), "1234", check))
    )
    if fault == "fifo":
        root.find(".//ItemsAndInventoryPreferences/FIFOEnabled").text = "true"
    if fault == "lot":
        root.find(".//ItemsAndInventoryPreferences/IsTrackingSerialOrLotNumber").text = "LotNumber"
    if fault == "negative":
        root.find(".//ItemInventoryRet/QuantityOnHand").text = "-1"
    if fault == "zero-cost":
        root.find(".//ItemInventoryRet/AverageCost").text = "0"
    if fault == "missing-cost":
        item = root.find(".//ItemInventoryRet")
        item.remove(item.find("AverageCost"))
    if fault == "source-quantity":
        root.find(".//InvoiceLineRet/Quantity").text = "1"
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.parametrize("missing", ["before", "after", "both"])
def test_return_cannot_pass_when_stock_baselines_are_missing(credit_case, missing):
    policy = Config.load(credit_case[0]).companies["company-a"]
    payload = credit_case[2]
    before = {
        "source_invoice": payload["invoice_txn_id"],
        "customer_balance": "25",
        "stock": {
            "service-id": {"quantity_on_hand": "0", "return_quantity": "2", "average_cost": "5.00"}
        },
    }
    after = copy.deepcopy(before)
    after["customer_balance"] = "15"
    after["stock"]["service-id"]["quantity_on_hand"] = "2"
    if missing in ("before", "both"):
        before.pop("stock")
    if missing in ("after", "both"):
        after.pop("stock")
    with pytest.raises(BridgeError, match="baseline or observation missing"):
        verify_balance_effect(payload, before, after, inventory=plan(policy, payload)["inventory"])


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_native_return_read_allowlist(credit_case, tmp_path, receipt):
    original.test_native_credit_query_gate(credit_case, tmp_path, receipt)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_return_write_allowlist(credit_case, tmp_path):
    original.test_native_credit_write_gate(credit_case, tmp_path)
