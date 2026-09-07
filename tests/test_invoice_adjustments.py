"""Adjustment scope, rounding, exact native masters/receipts and durable execution."""

# ruff: noqa: F401, F811
import copy
import json
import os
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.dispatch import amount
from kaydbooks_bridge.invoice_adjustments import native_lines, subtotal
from kaydbooks_bridge.invoice_compatibility import append_queries, plan, validate_response
from kaydbooks_bridge.invoice_receipt import add_request, validate_receipt
from kaydbooks_bridge.validation import validate_invoice
from test_direct_sdk import direct
from test_invoice_commercial import commercial, response
from test_invoice_compatibility import setup_invoice
from test_invoice_receipt import receipt_case, saved_receipt
from test_qbwc_discovery import discovery_setup


@pytest.fixture
def adjusted(receipt_case):
    policy, payload = receipt_case
    masters = copy.deepcopy(policy.invoice_masters)
    masters["items"].update(
        {
            "discount": {
                "kind": "Discount",
                "list_id": "discount-id",
                "income_account_id": "income-id",
            },
            "charge": {
                "kind": "OtherCharge",
                "list_id": "charge-id",
                "income_account_id": "income-id",
            },
        }
    )
    policy = replace(policy, items=(*policy.items, "discount", "charge"), invoice_masters=masters)
    payload["adjustments"] = [
        {"kind": "discount", "scope": "document", "item_id": "discount", "amount": "1.00"},
        {"kind": "charge", "scope": "document", "item_id": "charge", "amount": "2.00"},
    ]
    return policy, payload


def adjusted_response(request, mutate=None):
    def rows(data):
        data[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
        for kind, key in (("Account", "ar-id"), ("Customer", "customer-id")):
            data[(kind, key)].pop("CurrencyRef")
        data[("ItemDiscount", "discount-id")] = {
            "ListID": "discount-id",
            "IsActive": "true",
            "DiscountRate": "1.00",
            "AccountRef": {"ListID": "income-id"},
        }
        data[("ItemOtherCharge", "charge-id")] = {
            "ListID": "charge-id",
            "IsActive": "true",
            "SalesTaxCodeRef": {"ListID": "tax-code"},
            "SalesOrPurchase": {"AccountRef": {"ListID": "income-id"}, "Price": "2.00"},
        }
        if mutate:
            mutate(data)

    return response(request, taxable=False, mutate=rows)


def adjusted_receipt(policy, payload, operation="InvoiceQuery"):
    root = saved_receipt(operation)
    row = root[0][0][0]
    for name in ("Subtotal", "BalanceRemaining"):
        row.find(name).text = format(subtotal(payload), ".2f")
    for node in row.findall("InvoiceLineRet"):
        row.remove(node)
    for i, expected in enumerate(native_lines(payload)):
        line = ET.SubElement(row, "InvoiceLineRet")
        ET.SubElement(line, "TxnLineID").text = "line-" + str(i)
        ET.SubElement(ET.SubElement(line, "ItemRef"), "ListID").text = policy.invoice_masters[
            "items"
        ][expected["item_id"]]["list_id"]
        if not expected.get("adjustment"):
            ET.SubElement(line, "Quantity").text = expected["quantity"]
            ET.SubElement(line, "Rate").text = expected["unit_price"]
        ET.SubElement(line, "Amount").text = expected["amount"]
        ET.SubElement(ET.SubElement(line, "SalesTaxCodeRef"), "ListID").text = "tax-code"
    return root


def test_adjustment_rounding_placement_and_gross_limit(adjusted):
    policy, payload = adjusted
    payload["lines"] *= 3
    validate_invoice(payload, policy)
    lines = native_lines(payload)
    assert [line["amount"] for line in lines] == [
        "10.00",
        "-0.34",
        "10.00",
        "-0.33",
        "10.00",
        "-0.33",
        "2.00",
    ]
    assert subtotal(payload) == Decimal("31.00")
    assert amount({"operation": "invoice.create", "payload": payload}) == Decimal("32.00")
    with pytest.raises(BridgeError, match="bounded gross"):
        validate_invoice(payload, replace(policy, max_total="31.50"))
    payload["adjustments"][0].update(scope="line", line_number=2)
    assert [line["amount"] for line in native_lines(payload)] == [
        "10.00",
        "10.00",
        "-1.00",
        "10.00",
        "2.00",
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "over",
        "zero",
        "missing",
        "bool",
        "duplicate",
        "mixed",
        "tax",
        "kind",
        "raw",
        "charge_as_base",
        "net_zero",
    ],
)
def test_unreviewable_adjustments_rejected(adjusted, fault):
    policy, payload = adjusted
    a = payload["adjustments"][0]
    if fault == "over":
        a["amount"] = "10.01"
    elif fault == "zero":
        a["amount"] = "0.00"
    elif fault == "missing":
        a.update(scope="line", line_number=2)
    elif fault == "bool":
        a.update(scope="line", line_number=True)
    elif fault == "duplicate":
        payload["adjustments"].append(a.copy())
    elif fault == "mixed":
        payload["adjustments"].append({**a, "scope": "line", "line_number": 1})
    elif fault == "tax":
        payload["tax_amount"] = "1.00"
    elif fault == "kind":
        a["item_id"] = "charge"
    elif fault == "raw":
        a["qbxml"] = "anything"
    elif fault == "charge_as_base":
        payload["lines"][0]["item_id"] = "charge"
    elif fault == "net_zero":
        a["amount"] = "10.00"
        payload["adjustments"].pop()
    with pytest.raises(BridgeError):
        plan(policy, payload)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "inactive",
        "account",
        "percentage",
        "special",
        "tax",
        "currency",
        "omitted-charge-code",
    ],
)
def test_exact_adjustment_master_checks(adjusted, fault):
    policy, payload = adjusted
    check = plan(policy, payload)
    request = append_queries(
        '<QBXML><QBXMLMsgsRq><HostQueryRq requestID="9811"/><CompanyQueryRq requestID="9812"/></QBXMLMsgsRq></QBXML>',
        "981",
        check,
    )

    def mutate(rows):
        discount, charge = (
            rows[("ItemDiscount", "discount-id")],
            rows[("ItemOtherCharge", "charge-id")],
        )
        if fault == "inactive":
            discount["IsActive"] = "false"
        elif fault == "account":
            discount["AccountRef"]["ListID"] = "other"
        elif fault == "percentage":
            discount["DiscountRatePercent"] = "10"
        elif fault == "special":
            charge["SpecialItemType"] = "Payroll"
        elif fault == "tax":
            charge["SalesTaxCodeRef"]["ListID"] = "other"
        elif fault == "currency":
            rows[("Account", "income-id")]["CurrencyRef"] = {"ListID": "usd-id"}
        elif fault == "omitted-charge-code":
            charge.pop("SalesTaxCodeRef")

    result = adjusted_response(request, mutate)
    if fault and fault != "omitted-charge-code":
        with pytest.raises(BridgeError):
            validate_response(result, "981", check)
    else:
        validate_response(result, "981", check)


@pytest.mark.parametrize("fault", [None, "sign", "amount", "placement", "missing", "item"])
def test_saved_adjustment_receipt_requires_exact_signed_lines(adjusted, fault):
    policy, payload = adjusted
    request = ET.fromstring(add_request(policy, payload, "981"))
    lines = request[0][0][0].findall("InvoiceLineAdd")
    assert [line.findtext("Amount") for line in lines] == [None, "-1.00", "2.00"]
    assert lines[1].find("Rate") is None and lines[1].find("Quantity") is None
    root = adjusted_receipt(policy, payload)
    row = root[0][0][0]
    saved = row.findall("InvoiceLineRet")
    if fault == "sign":
        saved[1].find("Amount").text = "1.00"
    elif fault == "amount":
        saved[1].find("Amount").text = "-0.99"
    elif fault == "placement":
        row.remove(saved[1])
        row.append(saved[1])
    elif fault == "missing":
        row.remove(saved[1])
    elif fault == "item":
        saved[1].find("ItemRef/ListID").text = "charge-id"
    if fault:
        with pytest.raises(BridgeError):
            validate_receipt(ET.tostring(root), policy, payload, "981", txn_id="saved-id")
    else:
        assert validate_receipt(ET.tostring(root), policy, payload, "981")["subtotal"] == "11.00"


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_adjustment_native_write_gate(adjusted, tmp_path):
    from test_sample_posting import test_native_snapshot_gate_rejects_changes

    test_native_snapshot_gate_rejects_changes(adjusted, tmp_path)


@pytest.mark.parametrize("fault", [None, "lost-response", "permission", "stale-account"])
def test_adjusted_invoice_durable_lifecycle(adjusted, commercial, monkeypatch, fault):
    import test_receipt_lifecycle
    from kaydbooks_bridge.direct_sdk import discover
    from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
    from kaydbooks_bridge.reports import register
    from kaydbooks_bridge.sample_posting import post, reconcile
    from kaydbooks_bridge.service import Bridge
    from test_qbwc_discovery import PASSWORD_A
    from test_receipt_lifecycle import receipt_exchange

    policy, payload = adjusted
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] += ["submit", "post-sample", "report"]
    raw["companies"]["company-a"].update(
        items=list(policy.items),
        invoice_masters=policy.invoice_masters,
        approval_required=False,
        sample_posting={
            "connector": "connector-company-a",
            "authorization": "Synthetic adjustment test",
            "ref_prefix": "SYN-",
            "max_invoices": 1,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    discover(
        DurableQBWCDiscoveryService.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "981",
        invoice_check=payload,
        exchange=lambda rq, dest: dest.write_text(adjusted_response(rq)),
    )
    bridge = Bridge(path)
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        payload=payload,
        master_evidence={
            "transport": "direct-sdk",
            "connector": "connector-company-a",
            "id": "981",
        },
    )
    job = bridge.prepare(token, "company-a", envelope)
    bridge.action(token, "company-a", job["id"], "validate")
    preview = bridge.preview(token, "company-a", job["id"])
    assert preview["total"] == "11.00" and preview["discount_total"] == "1.00"
    assert preview["charge_total"] == "2.00" and preview["gross_subtotal"] == "10.00"
    bridge.action(token, "company-a", job["id"], "submit")
    writes = []

    def session(request, write, folder, approve):
        rq = ET.fromstring(request)
        collision = rq[0][-1]
        rq[0].remove(collision)
        root = ET.fromstring(adjusted_response(ET.tostring(rq, encoding="unicode")))
        if writes:
            rs = adjusted_receipt(policy, payload)[0][0]
            rs.set("requestID", collision.get("requestID"))
        else:
            rs = ET.Element(
                "InvoiceQueryRs",
                requestID=collision.get("requestID"),
                statusCode="500",
                statusSeverity="Warn",
            )
        root[0].append(rs)
        if fault == "permission":
            raw["principals"][actor]["companies"]["company-a"].remove("post-sample")
            path.write_text(json.dumps(raw))
        if fault == "stale-account":
            root.find(".//ItemDiscountRet/AccountRef/ListID").text = "wrong"
        allowed = approve(ET.tostring(root, encoding="unicode"))
        if write is None or not allowed:
            return None
        writes.append(write)
        if fault == "lost-response":
            raise RuntimeError("response lost")
        result = adjusted_receipt(policy, payload, "InvoiceAdd")
        result[0][0].set("requestID", ET.fromstring(write)[0][0].get("requestID"))
        return ET.tostring(result, encoding="unicode")

    monkeypatch.setattr(
        test_receipt_lifecycle, "saved_receipt", lambda: adjusted_receipt(policy, payload)
    )
    if fault in ("permission", "stale-account"):
        with pytest.raises(BridgeError):
            post(
                bridge,
                token,
                "company-a",
                job["id"],
                exchange=session,
                read_exchange=receipt_exchange(),
            )
        assert not writes
        return
    if fault == "lost-response":
        with pytest.raises(RuntimeError, match="response lost"):
            post(bridge, token, "company-a", job["id"], exchange=session)
        result = reconcile(
            Bridge(path),
            token,
            "company-a",
            job["id"],
            exchange=session,
            read_exchange=receipt_exchange(),
        )
    else:
        result = post(
            bridge,
            token,
            "company-a",
            job["id"],
            exchange=session,
            read_exchange=receipt_exchange(),
        )
    assert result["state"] == "verified" and len(writes) == 1
    with pytest.raises(BridgeError, match="never retry"):
        post(Bridge(path), token, "company-a", job["id"], exchange=session)
    assert register(bridge, token, "company-a", "2026-09-01", "2026-09-30")["total"] == "11.00"
