"""Explicit supplier adjustment accounts, signs, scopes and uncertain-write recovery."""

# ruff: noqa: F401, F811
import copy
import os
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

import test_sample_bills
from kaydbooks_bridge.bill_adjustments import expense_lines, subtotal
from kaydbooks_bridge.bill_lookup import plan, validate_check
from kaydbooks_bridge.bill_receipt import add_request, validate_receipt
from kaydbooks_bridge.bills import validate_payload
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.dispatch import amount
from kaydbooks_bridge.sample_bill_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_bill_lookup import exact_case, exact_response
from test_direct_sdk import direct
from test_qbwc_discovery import discovery_setup
from test_sample_bills import Session, queue_case, saved_bill


@pytest.fixture
def adjusted_bill(exact_case):
    path, token, payload = exact_case
    payload["adjustments"] = [
        {"kind": "discount", "scope": "document", "expense_id": "office", "amount": "1.00"},
        {
            "kind": "charge",
            "scope": "line",
            "line_number": 1,
            "expense_id": "office",
            "amount": "3.00",
        },
    ]
    return path, token, payload


def receipt(request_id, **kwargs):
    root = saved_bill(request_id, **kwargs)
    row = root[0][0][0]
    row.find("AmountDue").text = "12.00"
    row.find("OpenAmount").text = "12.00"
    for i, (value, memo) in enumerate((("-1.00", "Document discount"), ("3.00", "Line 1 charge"))):
        line = ET.SubElement(row, "ExpenseLineRet")
        ET.SubElement(line, "TxnLineID").text = "adjustment-" + str(i)
        ET.SubElement(ET.SubElement(line, "AccountRef"), "ListID").text = "E-A"
        ET.SubElement(line, "Amount").text = value
        ET.SubElement(line, "Memo").text = memo
    return root


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing-line",
        "negative",
        "over",
        "net-zero",
        "unknown-account",
        "duplicate",
        "mixed",
        "memo",
        "too-many",
    ],
)
def test_bill_adjustment_validation_and_request(adjusted_bill, fault):
    path, _, payload = adjusted_bill
    policy = Config.load(path).companies["company-a"]
    a = payload["adjustments"][0]
    if fault == "missing-line":
        a.update(scope="line", line_number=2)
    elif fault == "negative":
        a["amount"] = "-1.00"
    elif fault == "over":
        a["amount"] = "10.01"
    elif fault == "net-zero":
        a["amount"] = "10.00"
        payload["adjustments"].pop()
    elif fault == "unknown-account":
        a["expense_id"] = "other"
    elif fault == "duplicate":
        payload["adjustments"].append(copy.deepcopy(a))
    elif fault == "mixed":
        payload["adjustments"].append({**a, "scope": "line", "line_number": 1})
    elif fault == "memo":
        a["memo"] = "unreviewed native field"
    elif fault == "too-many":
        payload["lines"] *= 99
    if fault:
        with pytest.raises(BridgeError):
            validate_payload(payload, policy)
        return
    assert subtotal(validate_payload(payload, policy)) == Decimal("12.00")
    assert amount({"operation": "bill.create", "payload": payload}) == Decimal("13.00")
    root = ET.fromstring(add_request(policy, payload, "123"))
    lines = root[0][0][0].findall("ExpenseLineAdd")
    assert [line.findtext("Amount") for line in lines] == ["10.00", "-1.00", "3.00"]
    assert [line.findtext("Memo") for line in lines] == [None, "Document discount", "Line 1 charge"]


@pytest.mark.parametrize("fault", [None, "amount", "sign", "memo", "account", "missing"])
def test_bill_adjustment_saved_fields(adjusted_bill, fault):
    path, _, payload = adjusted_bill
    policy = Config.load(path).companies["company-a"]
    root = receipt("123")
    row = root[0][0][0]
    line = row.findall("ExpenseLineRet")[1]
    if fault == "amount":
        line.find("Amount").text = "-0.99"
    elif fault == "sign":
        line.find("Amount").text = "1.00"
    elif fault == "memo":
        line.find("Memo").text = "Line 1 discount"
    elif fault == "account":
        line.find("AccountRef/ListID").text = "OTHER"
    elif fault == "missing":
        row.remove(line)
    if fault:
        with pytest.raises(BridgeError):
            validate_receipt(ET.tostring(root), policy, payload, "123")
    else:
        result = validate_receipt(ET.tostring(root), policy, payload, "123")
        assert (
            result["total"] == "12.00"
            and result["adjustment_expense_lines"][0]["amount"] == "-1.00"
        )


@pytest.mark.parametrize("lost", [False, True])
def test_bill_adjustment_lifecycle_and_no_resend(adjusted_bill, monkeypatch, lost):
    bridge, token, job, payload = queue_case(adjusted_bill)
    monkeypatch.setattr(test_sample_bills, "saved_bill", receipt)
    original = test_sample_bills.receipt_exchange

    def read(request, dest):
        original(request, dest)
        root = ET.fromstring(dest.read_text())
        root.find(".//BillToPay/AmountDue").text = "12.00"
        dest.write_text(ET.tostring(root, encoding="unicode"))

    session = Session(crash="after" if lost else None)
    if lost:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job, exchange=session)
        result = reconcile(
            Bridge(bridge.config_path),
            token,
            "company-a",
            job,
            exchange=session,
            read_exchange=read,
        )
    else:
        result = post(bridge, token, "company-a", job, exchange=session, read_exchange=read)
    assert result["state"] == "verified" and session.writes == 1
    assert result["transaction_receipt"]["receipt"]["total"] == "12.00"
    with pytest.raises(BridgeError, match="never retry"):
        post(Bridge(bridge.config_path), token, "company-a", job, exchange=session)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_bill_adjustment_native_gate(adjusted_bill, tmp_path):
    from test_sample_bills import test_native_bill_write_allowlist

    test_native_bill_write_allowlist(queue_case(adjusted_bill), tmp_path, False, False)


def test_adjustment_only_account_is_queried_and_type_checked(adjusted_bill):
    from dataclasses import replace

    from kaydbooks_bridge.bill_lookup import append_check

    path, _, payload = adjusted_bill
    policy = Config.load(path).companies["company-a"]
    policy = replace(
        policy,
        bill_masters={
            **policy.bill_masters,
            "expenses": {**policy.bill_masters["expenses"], "adjustment-only": "IN-A"},
        },
    )
    payload["adjustments"][0]["expense_id"] = "adjustment-only"
    check = plan(policy, payload)
    assert ("Account", "IN-A") in check["queries"]
    request = append_check(
        '<QBXML><QBXMLMsgsRq><HostQueryRq requestID="1231"/><CompanyQueryRq requestID="1232"/></QBXMLMsgsRq></QBXML>',
        "123",
        check,
    )
    with pytest.raises(BridgeError, match="expense account type"):
        validate_check(exact_response(request), "123", check)


def test_adjustment_revocation_stops_before_bill_write(adjusted_bill):
    bridge, token, job, _ = queue_case(adjusted_bill)
    session = Session(before=lambda: bridge.pause(token, "company-a", True))
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job, exchange=session)
    assert session.writes == 0
