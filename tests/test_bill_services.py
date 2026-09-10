"""Purchased service bills preserve item/account and payable evidence."""

# ruff: noqa: F811
import json
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.bill_lookup import append_check, plan, validate_check
from kaydbooks_bridge.bill_receipt import add_request, validate_receipt
from kaydbooks_bridge.bills import validate_payload
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_bill_posting import post, reconcile
from test_bill_lookup import exact_case, exact_response  # noqa: F401
from test_direct_sdk import PASSWORD_A, direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_sample_bills import Session, queue_case, receipt_exchange, saved_bill


@pytest.fixture
def service_case(exact_case):
    path, token, payload = exact_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["items"] = {
        "service": {"list_id": "I-A", "type": "service", "expense_id": "office"}
    }
    path.write_text(json.dumps(raw))
    payload["lines"] = [
        {"expense_id": "office", "amount": "5.00"},
        {"item_id": "service", "quantity": "2", "cost": "2.50", "amount": "5.00"},
    ]
    return path, token, payload


def mixed_saved(root):
    row = root.find(".//BillRet")
    row.find("ExpenseLineRet/Amount").text = "5.00"
    item = ET.SubElement(row, "ItemLineRet")
    ET.SubElement(item, "TxnLineID").text = "item-line"
    ET.SubElement(ET.SubElement(item, "ItemRef"), "ListID").text = "I-A"
    for name, value in [("Quantity", "2"), ("Cost", "2.50"), ("Amount", "5.00")]:
        ET.SubElement(item, name).text = value
    return root


def read_mixed(request, dest):
    receipt_exchange(request, dest)
    dest.write_text(ET.tostring(mixed_saved(ET.fromstring(dest.read_text())), encoding="unicode"))


class MixedSession(Session):
    def __call__(self, request, write, folder, approve):
        def mixed_approve(xml):
            root = ET.fromstring(xml)
            if root.find(".//BillRet") is not None:
                mixed_saved(root)
            return approve(ET.tostring(root, encoding="unicode"))

        result = super().__call__(request, write, folder, mixed_approve)
        return (
            ET.tostring(mixed_saved(ET.fromstring(result)), encoding="unicode") if result else None
        )


@pytest.mark.parametrize("crash", [False, True])
def test_mixed_bill_native_lifecycle_and_recovery(service_case, crash):
    bridge, token, job_id, _ = queue_case(service_case)
    session = MixedSession(crash="after" if crash else None)
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session)
        result = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=read_mixed
        )
    else:
        result = post(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=read_mixed
        )
    assert result["state"] == "verified" and session.writes == 1
    receipt = result["transaction_receipt"]["receipt"]
    assert (
        receipt["line_ids"] == ["bill-line", "item-line"]
        and receipt["outstanding_amount"] == "10.00"
    )
    assert bridge.audit(token, "company-a")["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "1000001"),
        ("quantity", "NaN"),
        ("cost", "0.00"),
        ("amount", "6.00"),
        ("item_id", "unknown"),
    ],
)
def test_invalid_service_line_is_rejected(service_case, field, value):
    path, _, payload = service_case
    payload["lines"][1][field] = value
    with pytest.raises(BridgeError):
        validate_payload(payload, Config.load(path).companies["company-a"])


@pytest.mark.parametrize("mutation", ["account", "inactive", "unit", "tax", "ambiguous"])
def test_service_master_checks_fail_closed(service_case, mutation):
    path, _, payload = service_case
    check = plan(Config.load(path).companies["company-a"], payload)
    request = append_check(S._discovery_request("881", "17.0"), "881", check)
    root = ET.fromstring(exact_response(request))
    item = root.find(".//ItemServiceRet")
    if mutation == "account":
        item.find("SalesAndPurchase/ExpenseAccountRef/ListID").text = "WRONG"
    elif mutation == "inactive":
        item.find("IsActive").text = "false"
    elif mutation == "unit":
        ET.SubElement(item, "UnitOfMeasureSetRef")
    elif mutation == "tax":
        ET.SubElement(item, "IsTaxIncluded").text = "true"
    else:
        ET.SubElement(item, "SalesOrPurchase")
    with pytest.raises(BridgeError):
        validate_check(ET.tostring(root), "881", check)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ItemRef/ListID", "WRONG"),
        ("Quantity", "3"),
        ("Cost", "3.00"),
        ("Amount", "6.00"),
        ("TxnLineID", "bill-line"),
    ],
)
def test_saved_service_line_must_match(service_case, field, value):
    path, _, payload = service_case
    root = mixed_saved(saved_bill("8833"))
    root.find(".//ItemLineRet/" + field).text = value
    with pytest.raises(BridgeError):
        validate_receipt(
            ET.tostring(root), Config.load(path).companies["company-a"], payload, "8833"
        )


def test_service_preview_is_bounded_read_only(service_case):
    path, token, _ = service_case
    result = discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "884",
        bill_services=True,
        exchange=lambda rq, d: d.write_text(exact_response(rq)),
    )
    assert result["operation"] == "bill-services-preview" and result["complete"] is False
    assert result["masters"][0]["ListID"] == "I-A"
    with pytest.raises(BridgeError):
        discover(
            S.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            "885",
            bill_services=True,
            bill_terms=True,
        )


def test_item_builder_preserves_schema_order(service_case):
    path, _, payload = service_case
    policy = Config.load(path).companies["company-a"]
    payload["lines"].reverse()
    bill = ET.fromstring(add_request(policy, payload, "8863")).find(".//BillAdd")
    assert [n.tag for n in bill][-2:] == ["ExpenseLineAdd", "ItemLineAdd"]
    assert [n.tag for n in bill.find("ItemLineAdd")] == ["ItemRef", "Quantity", "Cost", "Amount"]
