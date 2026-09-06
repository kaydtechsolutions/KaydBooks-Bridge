"""Fixed expense-bill XML and exact saved-record comparison; no dispatch."""

import re
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .bill_lookup import plan
from .config import BridgeError
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import digest

RECEIPT_FIELDS = (
    "TxnID",
    "EditSequence",
    "VendorRef",
    "APAccountRef",
    "TxnDate",
    "DueDate",
    "RefNumber",
    "TermsRef",
    "AmountDue",
    "OpenAmount",
    "CurrencyRef",
    "ExchangeRate",
    "AmountDueInHomeCurrency",
    "IsPaid",
    "IsTaxIncluded",
    "SalesTaxCodeRef",
    "LinkedTxn",
    "ExpenseLineRet",
    "ItemLineRet",
    "ItemGroupLineRet",
)


def correlation(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        raise BridgeError("invalid bill request correlation")
    return value


def render(root):
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def add_request(policy, payload, request_id):
    binding = plan(policy, payload)["binding"]
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    add = ET.SubElement(
        ET.SubElement(batch, "BillAddRq", requestID=correlation(request_id)), "BillAdd"
    )
    for name, key in (("VendorRef", "vendor_list_id"), ("APAccountRef", "payable_list_id")):
        ET.SubElement(ET.SubElement(add, name), "ListID").text = binding[key]
    for name, key in (
        ("TxnDate", "txn_date"),
        ("DueDate", "due_date"),
        ("RefNumber", "ref_number"),
    ):
        ET.SubElement(add, name).text = payload[key]
    if "terms_list_id" in binding:
        ET.SubElement(ET.SubElement(add, "TermsRef"), "ListID").text = binding["terms_list_id"]
    for line, list_id in zip(payload["lines"], binding["expense_list_ids"], strict=True):
        if "item_id" in line:
            continue
        node = ET.SubElement(add, "ExpenseLineAdd")
        ET.SubElement(ET.SubElement(node, "AccountRef"), "ListID").text = list_id
        ET.SubElement(node, "Amount").text = line["amount"]
    for line, item_id in zip(
        payload["lines"], binding.get("item_list_ids", [None] * len(payload["lines"])), strict=True
    ):
        if item_id is None:
            continue
        node = ET.SubElement(add, "ItemLineAdd")
        ET.SubElement(ET.SubElement(node, "ItemRef"), "ListID").text = item_id
        for name, key in (("Quantity", "quantity"), ("Cost", "cost"), ("Amount", "amount")):
            ET.SubElement(node, name).text = line[key]
    return render(root)


def append_lookup(discovery, run, txn_id, policy, payload):
    required_id(txn_id)
    root = fromstring(discovery)
    query = ET.SubElement(root[0], "BillQueryRq", requestID=correlation(run + "3"))
    ET.SubElement(query, "TxnID").text = txn_id
    query_fields(query)
    binding = plan(policy, payload)["binding"]
    payable = ET.SubElement(root[0], "BillToPayQueryRq", requestID=correlation(run + "4"))
    for name, key in (("PayeeEntityRef", "vendor_list_id"), ("APAccountRef", "payable_list_id")):
        ET.SubElement(ET.SubElement(payable, name), "ListID").text = binding[key]
    return render(root)


def query_fields(query):
    ET.SubElement(query, "IncludeLineItems").text = "true"
    ET.SubElement(query, "IncludeLinkedTxns").text = "true"
    for field in RECEIPT_FIELDS:
        ET.SubElement(query, "IncludeRetElement").text = field


def lookup_context(policy, payload, txn_id):
    required_id(txn_id)
    return digest(
        {
            "operation": "bill-receipt-check",
            "balance_contract": "bill-to-pay-v1",
            "bill": plan(policy, payload)["context_sha256"],
            "txn_id": txn_id,
        }
    )


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 4:
        raise BridgeError("bill receipt requires discovery, exact bill and payable responses")
    receipt_root = ET.Element("QBXML")
    ET.SubElement(receipt_root, "QBXMLMsgsRs").append(root[0][2])
    proof = validate_receipt(ET.tostring(receipt_root), policy, payload, run + "3", txn_id=txn_id)
    validate_payable(root[0][3], policy, payload, run + "4", txn_id)
    proof.update(
        balance_verification="matched-bill-to-pay",
        outstanding_amount=proof["total"],
        scope="unpaid-service-expense-bill-receipt"
        if "item_list_ids" in plan(policy, payload)["binding"]
        else "unpaid-expense-bill-receipt",
    )
    root[0].remove(root[0][3])
    root[0].remove(root[0][2])
    return ET.tostring(root, encoding="unicode"), proof


def validate_payable(rs, policy, payload, request_id, txn_id):
    """Match one unpaid bill in a complete vendor/AP-scoped payment query."""
    if (
        rs.tag != "BillToPayQueryRs"
        or rs.get("requestID") != correlation(request_id)
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or rs.get("iteratorRemainingCount") not in (None, "0")
        or not 1 <= len(rs) <= 1000
    ):
        raise BridgeError("bill payable query is incomplete or unsuccessful")
    binding = plan(policy, payload)["binding"]
    matches, seen = [], set()
    for entry in rs:
        if (
            entry.tag != "BillToPayRet"
            or len(entry) != 1
            or entry[0].tag not in ("BillToPay", "CreditToApply")
        ):
            raise BridgeError("ambiguous bill payable entry")
        row = entry[0]
        ids = row.findall("TxnID")
        if len(ids) != 1 or len(ids[0]):
            raise BridgeError("missing bill payable identity")
        actual = required_id(ids[0].text)
        if actual in seen:
            raise BridgeError("duplicate bill payable identity")
        seen.add(actual)
        if actual == txn_id:
            matches.append(row)
    if len(matches) != 1 or matches[0].tag != "BillToPay":
        raise BridgeError("exact unpaid bill is absent or ambiguous")
    row = matches[0]
    expected = {
        "TxnType": "Bill",
        "APAccountRef/ListID": binding["payable_list_id"],
        "TxnDate": payload["txn_date"],
        "DueDate": payload["due_date"],
        "RefNumber": payload["ref_number"],
    }
    for path, value in expected.items():
        nodes = row.findall(path)
        if len(nodes) != 1 or len(nodes[0]) or nodes[0].text != value:
            raise BridgeError("bill payable identity or dates differ")
    total = sum(Decimal(line["amount"]) for line in payload["lines"])
    for field, amount in (
        ("AmountDue", total),
        ("ExchangeRate", Decimal(1)),
        ("AmountDueInHomeCurrency", total),
    ):
        nodes = row.findall(field)
        if field != "AmountDue" and not nodes:
            continue
        if len(nodes) != 1 or len(nodes[0]) or decimal_evidence(nodes[0].text) != amount:
            raise BridgeError("bill payable amount differs")
    if row.find("CurrencyRef") is not None:
        raise BridgeError("unsupported bill payable currency")


def validate_receipt(xml, policy, payload, request_id, *, operation="BillQuery", txn_id=None):
    binding = plan(policy, payload)["binding"]
    if operation not in ("BillQuery", "BillAdd"):
        raise BridgeError("unsupported bill receipt operation")
    if txn_id is not None:
        required_id(txn_id)
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 1:
        raise BridgeError("exactly one bill response required")
    rs = root[0][0]
    if (
        rs.tag != operation + "Rs"
        or rs.get("requestID") != correlation(request_id)
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != "BillRet"
    ):
        raise BridgeError("unsuccessful, ambiguous or uncorrelated bill receipt")
    row = rs[0]

    def value(node, path):
        for name in path.split("/"):
            children = node.findall(name)
            if len(children) != 1:
                raise BridgeError("missing or ambiguous bill receipt field")
            node = children[0]
        if len(node) or node.text is None:
            raise BridgeError("malformed bill receipt field")
        return node.text.strip()

    def equals(node, path, expected):
        if value(node, path) != expected:
            raise BridgeError("saved bill differs from approved request")

    def number(node, path, expected):
        if decimal_evidence(value(node, path)) != Decimal(expected):
            raise BridgeError("saved bill amount differs")

    actual = required_id(value(row, "TxnID"))
    if txn_id is not None and actual != txn_id:
        raise BridgeError("bill transaction identity mismatch")
    edit = value(row, "EditSequence")
    if not re.fullmatch(r"[0-9]{1,16}", edit):
        raise BridgeError("invalid bill edit sequence")
    for name, key in (
        ("VendorRef/ListID", "vendor_list_id"),
        ("APAccountRef/ListID", "payable_list_id"),
    ):
        equals(row, name, binding[key])
    for name, key in (
        ("TxnDate", "txn_date"),
        ("DueDate", "due_date"),
        ("RefNumber", "ref_number"),
    ):
        equals(row, name, payload[key])
    equals(row, "IsPaid", "false")
    if "terms_list_id" in binding:
        equals(row, "TermsRef/ListID", binding["terms_list_id"])
    if any(
        row.find(name) is not None
        for name in (
            "LinkedTxn",
            "CurrencyRef",
            "SalesTaxCodeRef",
            "ItemGroupLineRet",
        )
    ):
        raise BridgeError("unsupported saved bill feature")
    if row.find("IsTaxIncluded") is not None:
        equals(row, "IsTaxIncluded", "false")
    if row.find("ExchangeRate") is not None:
        number(row, "ExchangeRate", "1")
    total = sum(Decimal(line["amount"]) for line in payload["lines"])
    number(row, "AmountDue", total)
    # BillRet.OpenAmount can reflect the vendor balance in the qualified Desktop
    # version. Preserve it as an observation, never as this bill's outstanding
    # amount. Only validate_lookup's independently matched BillToPay can prove that.
    observed_open = None
    if row.find("OpenAmount") is not None:
        observed_open = format(decimal_evidence(value(row, "OpenAmount")), ".2f")
    if row.find("AmountDueInHomeCurrency") is not None:
        number(row, "AmountDueInHomeCurrency", total)
    lines = row.findall("ExpenseLineRet")
    expense_pairs = [
        (line, list_id)
        for line, list_id in zip(payload["lines"], binding["expense_list_ids"], strict=True)
        if "expense_id" in line
    ]
    if len(lines) != len(expense_pairs):
        raise BridgeError("saved bill line count differs")
    ids = []
    for saved, (line, list_id) in zip(lines, expense_pairs, strict=True):
        ids.append(required_id(value(saved, "TxnLineID")))
        equals(saved, "AccountRef/ListID", list_id)
        number(saved, "Amount", line["amount"])
        if saved.find("BillableStatus") is not None:
            equals(saved, "BillableStatus", "NotBillable")
        if any(
            saved.find(name) is not None
            for name in ("CustomerRef", "ClassRef", "SalesTaxCodeRef", "SalesRepRef")
        ):
            raise BridgeError("unsupported saved bill line feature")
        if saved.find("TaxAmount") is not None:
            number(saved, "TaxAmount", "0")
    item_pairs = [
        (line, item_id)
        for line, item_id in zip(
            payload["lines"],
            binding.get("item_list_ids", [None] * len(payload["lines"])),
            strict=True,
        )
        if item_id is not None
    ]
    item_lines = row.findall("ItemLineRet")
    if len(item_lines) != len(item_pairs):
        raise BridgeError("saved bill item line count differs")
    for saved, (line, item_id) in zip(item_lines, item_pairs, strict=True):
        ids.append(required_id(value(saved, "TxnLineID")))
        equals(saved, "ItemRef/ListID", item_id)
        for name, key in (("Quantity", "quantity"), ("Cost", "cost"), ("Amount", "amount")):
            number(saved, name, line[key])
        if saved.find("BillableStatus") is not None:
            equals(saved, "BillableStatus", "NotBillable")
        if any(
            saved.find(name) is not None
            for name in (
                "CustomerRef",
                "ClassRef",
                "SalesTaxCodeRef",
                "SalesRepRef",
                "UnitOfMeasure",
                "InventorySiteRef",
                "InventorySiteLocationRef",
                "SerialNumber",
                "LotNumber",
                "OverrideItemAccountRef",
                "LinkToTxn",
            )
        ):
            raise BridgeError("unsupported saved bill item feature")
        if saved.find("TaxAmount") is not None:
            number(saved, "TaxAmount", "0")
    if len(set(ids)) != len(ids):
        raise BridgeError("duplicate bill line identity")
    return {
        "operation": "bill.create",
        "txn_id": actual,
        "edit_sequence": edit,
        "ref_number": payload["ref_number"],
        "vendor_list_id": binding["vendor_list_id"],
        "total": format(total, ".2f"),
        "line_ids": ids,
        "verification": "matched-saved-bill",
        "scope": "expense-bill-fields",
        "observed_billret_open_amount": observed_open,
        "balance_verification": "requires-bill-to-pay",
    }
