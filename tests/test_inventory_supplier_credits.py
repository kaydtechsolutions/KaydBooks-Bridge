"""Inventory returns bind source-bill capacity and independent stock decrease."""

# ruff: noqa: F811
import copy
import json
import os
from decimal import Decimal
from xml.etree import ElementTree as E

import pytest

import test_supplier_credits as original
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_supplier_credit_posting import post, reconcile
from kaydbooks_bridge.supplier_credits import (
    append_check,
    plan,
    validate_check,
    verify_balance_effect,
)
from test_bill_lookup import exact_case  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_supplier_credits import Session, credit_case, queue_credit  # noqa: F401


def stock_line(row):
    for line in row.findall("ExpenseLineRet"):
        row.remove(line)
    item = E.SubElement(row, "ItemLineRet")
    E.SubElement(E.SubElement(item, "ItemRef"), "ListID").text = "INV-A"
    for k, v in (
        ("TxnLineID", "stock-line"),
        ("Quantity", "2"),
        ("Cost", "5.00"),
        ("Amount", "10.00"),
    ):
        E.SubElement(item, k).text = v
    return row


@pytest.fixture
def inventory_case(credit_case, monkeypatch):
    path, token, payload = credit_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["items"] = {
        "stock": {
            "list_id": "INV-A",
            "type": "inventory",
            "asset_list_id": "AS-A",
            "cogs_list_id": "CG-A",
            "income_list_id": "IN-A",
        }
    }
    path.write_text(json.dumps(raw))
    payload["lines"] = [{"item_id": "stock", "quantity": "2", "cost": "5.00", "amount": "10.00"}]
    credit = original.credit_row
    monkeypatch.setattr(original, "credit_row", lambda: stock_line(credit()))
    return path, token, payload


class StockSession(Session):
    def __init__(self):
        super().__init__()
        self.quantity = Decimal(2)
        self.wrong_stock = False
        self.mutate = self.observe

    def observe(self, root):
        row = root.find(".//ItemInventoryRet")
        if row is not None:
            row.find("QuantityOnHand").text = str(
                self.quantity - (0 if self.wrong_stock else 2 * len(self.credits))
            )
            row.find("AverageCost").text = "5.00"
        for bill in root.findall(".//BillRet"):
            stock_line(bill)
            bill.find("AmountDue").text = "10.00"


@pytest.mark.parametrize("crash", [False, True])
def test_return_exhausts_stock_and_reconciles_without_resending(inventory_case, crash):
    session = StockSession()
    bridge, token, job_id, session = queue_credit(inventory_case, session)
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        job = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    else:
        job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert job["state"] == "verified" and session.writes == 1
    effect = job["transaction_receipt"]["receipt"]["balance_effects"]["stock_effects"]["INV-A"]
    assert effect["before"] == "2" and effect["returned"] == "2" and effect["after"] == "0"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


@pytest.mark.parametrize("case", ["insufficient", "wrong-cost", "advanced", "missing"])
def test_return_preflight_rejects_unsupported_stock(inventory_case, case):
    path, _, payload = inventory_case
    check = plan(Config.load(path).companies["company-a"], payload)
    session = StockSession()
    root = E.fromstring(
        session.xml(append_check(S._discovery_request("1234", "17.0"), "1234", check))
    )
    row = root.find(".//ItemInventoryRet")
    if case == "insufficient":
        row.find("QuantityOnHand").text = "1"
    if case == "wrong-cost":
        row.find("AverageCost").text = "6.00"
    if case == "missing":
        row.remove(row.find("QuantityOnHand"))
    if case == "advanced":
        root.find(".//ItemsAndInventoryPreferences/FIFOEnabled").text = "true"
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


def test_wrong_stock_after_return_is_held(inventory_case):
    session = StockSession()
    session.wrong_stock = True
    bridge, token, job_id, session = queue_credit(inventory_case, session)
    with pytest.raises(BridgeError, match="stock effect differs"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


@pytest.mark.parametrize("missing", ["before", "after"])
def test_return_requires_original_and_current_stock(inventory_case, missing):
    payload = inventory_case[2]
    before = {
        "source_bill": "source-id",
        "vendor_balance": "10",
        "payables": {},
        "stock": {"INV-A": {"quantity_on_hand": "2", "return_quantity": "2", "average_cost": "5"}},
    }
    after = copy.deepcopy(before)
    after["vendor_balance"] = "0"
    after["payables"] = {"credit": {"kind": "CreditToApply", "amount": "10"}}
    after["stock"]["INV-A"]["quantity_on_hand"] = "0"
    (before if missing == "before" else after).pop("stock")
    with pytest.raises(BridgeError, match="baseline or observation"):
        verify_balance_effect(payload, before, after)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_inventory_return_native_query_gate(inventory_case, tmp_path, receipt):
    original.test_native_supplier_credit_query_gate(inventory_case, tmp_path, receipt)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_inventory_return_native_write_gate(inventory_case, tmp_path):
    original.test_native_supplier_credit_write_gate(inventory_case, tmp_path)
