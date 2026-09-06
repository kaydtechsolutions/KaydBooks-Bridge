"""Exact request construction and saved receipt comparison; no QuickBooks writes."""

import json
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.invoice_receipt import add_request, validate_receipt
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401


@pytest.fixture
def receipt_case(commercial):  # noqa: F811
    path, _, payload = commercial
    raw = json.loads(path.read_text())
    masters = raw["companies"]["company-a"]["invoice_masters"]
    masters["currency_id"] = None
    masters["commercial"].update(tax_item_id=None, tax_rate="0")
    path.write_text(json.dumps(raw))
    payload["tax_amount"] = "0.00"
    return Config.load(path).companies["company-a"], payload


def saved_receipt(operation="InvoiceQuery"):
    return ET.fromstring(f"""<QBXML><QBXMLMsgsRs><{operation}Rs requestID="981"
        statusCode="0" statusSeverity="Info"><InvoiceRet>
        <TxnID>saved-id</TxnID><EditSequence>1234</EditSequence>
        <CustomerRef><ListID>customer-id</ListID></CustomerRef>
        <ARAccountRef><ListID>ar-id</ListID></ARAccountRef>
        <TxnDate>2026-09-06</TxnDate><RefNumber>SYN-CHECK</RefNumber>
        <IsPending>false</IsPending><IsFinanceCharge>false</IsFinanceCharge>
        <IsToBePrinted>false</IsToBePrinted><IsToBeEmailed>false</IsToBeEmailed>
        <IsPaid>false</IsPaid><Subtotal>10.00</Subtotal><SalesTaxTotal>0.00</SalesTaxTotal>
        <AppliedAmount>0.00</AppliedAmount><BalanceRemaining>10.00</BalanceRemaining>
        <CustomerSalesTaxCodeRef><ListID>tax-code</ListID></CustomerSalesTaxCodeRef>
        <InvoiceLineRet><TxnLineID>line-id</TxnLineID><ItemRef><ListID>service-id</ListID></ItemRef>
        <Quantity>2</Quantity><Rate>5.00</Rate><Amount>10.00</Amount>
        <SalesTaxCodeRef><ListID>tax-code</ListID></SalesTaxCodeRef></InvoiceLineRet>
        </InvoiceRet></{operation}Rs></QBXMLMsgsRs></QBXML>""")


def test_request_has_only_reviewed_fields_and_does_not_send(receipt_case):
    policy, payload = receipt_case
    request = add_request(policy, payload, "981")
    root = ET.fromstring(request)
    assert len(root[0]) == 1 and root[0][0].tag == "InvoiceAddRq"
    add = root[0][0][0]
    assert [n.tag for n in add] == [
        "CustomerRef",
        "ARAccountRef",
        "TxnDate",
        "RefNumber",
        "IsPending",
        "IsFinanceCharge",
        "IsToBePrinted",
        "IsToBeEmailed",
        "CustomerSalesTaxCodeRef",
        "InvoiceLineAdd",
    ]
    assert add.findtext("IsToBeEmailed") == "false"
    assert add.findtext("InvoiceLineAdd/Quantity") == "2"
    assert add.findtext("InvoiceLineAdd/Rate") == "5.00"
    assert add.find("InvoiceLineAdd/Amount") is None  # QuickBooks computes; receipt must match.
    assert request == add_request(policy, payload, "981")


@pytest.mark.parametrize("operation", ["InvoiceAdd", "InvoiceQuery"])
def test_exact_saved_receipt_matches(receipt_case, operation):
    policy, payload = receipt_case
    result = validate_receipt(
        ET.tostring(saved_receipt(operation)),
        policy,
        payload,
        "981",
        operation=operation,
        txn_id="saved-id",
    )
    assert result["balance_remaining"] == "10.00"
    assert result["verification"] == "matched-saved-invoice"


@pytest.mark.parametrize(
    "path,value",
    [
        ("TxnID", "wrong"),
        ("CustomerRef/ListID", "wrong"),
        ("ARAccountRef/ListID", "wrong"),
        ("TxnDate", "2026-09-05"),
        ("RefNumber", "OTHER"),
        ("IsPending", "true"),
        ("IsToBeEmailed", "true"),
        ("Subtotal", "11"),
        ("SalesTaxTotal", "1"),
        ("AppliedAmount", "1"),
        ("BalanceRemaining", "9"),
        ("CustomerSalesTaxCodeRef/ListID", "other"),
        ("InvoiceLineRet/Quantity", "3"),
        ("InvoiceLineRet/Rate", "6"),
        ("InvoiceLineRet/Amount", "11"),
        ("InvoiceLineRet/ItemRef/ListID", "other"),
        ("InvoiceLineRet/SalesTaxCodeRef/ListID", "other"),
    ],
)
def test_changed_receipt_is_never_success(receipt_case, path, value):
    policy, payload = receipt_case
    root = saved_receipt()
    root.find("QBXMLMsgsRs/InvoiceQueryRs/InvoiceRet/" + path).text = value
    with pytest.raises(BridgeError):
        validate_receipt(ET.tostring(root), policy, payload, "981", txn_id="saved-id")


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "empty", "status", "correlation", "group", "linked", "doctype"]
)
def test_ambiguous_or_unsupported_receipts_fail(receipt_case, case):
    policy, payload = receipt_case
    root = saved_receipt()
    rs, row = root[0][0], root[0][0][0]
    if case == "missing":
        row.remove(row.find("SalesTaxTotal"))
    elif case == "duplicate":
        ET.SubElement(row, "CustomerRef").append(ET.Element("ListID"))
    elif case == "empty":
        rs.remove(row)
    elif case == "status":
        rs.set("statusCode", "1")
    elif case == "correlation":
        rs.set("requestID", "999")
    elif case == "group":
        ET.SubElement(row, "InvoiceLineGroupRet")
    elif case == "linked":
        ET.SubElement(row, "LinkedTxn")
    xml = ET.tostring(root, encoding="unicode")
    if case == "doctype":
        xml = '<!DOCTYPE QBXML [<!ENTITY test "untrusted">]>' + xml
    with pytest.raises(BridgeError):
        validate_receipt(xml, policy, payload, "981")


@pytest.mark.parametrize("case", ["taxable", "currency", "no-commercial", "correlation"])
def test_request_refuses_unqualified_modes(receipt_case, case):
    from dataclasses import replace

    policy, payload = receipt_case
    if case == "taxable":
        policy.invoice_masters["commercial"]["tax_item_id"] = "tax-item"
    elif case == "currency":
        policy.invoice_masters["currency_id"] = "usd-id"
    elif case == "no-commercial":
        policy = replace(policy, invoice_masters={})
    with pytest.raises(BridgeError):
        add_request(policy, payload, "bad" if case == "correlation" else "981")
