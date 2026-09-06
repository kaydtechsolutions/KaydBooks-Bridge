"""Pure non-taxable service invoice request/receipt helpers; no dispatch capability."""

import re
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import plan, required_id
from .validation import digest

RECEIPT_FIELDS = (
    "TxnID",
    "EditSequence",
    "CustomerRef",
    "ARAccountRef",
    "TxnDate",
    "RefNumber",
    "IsPending",
    "IsFinanceCharge",
    "Subtotal",
    "SalesTaxTotal",
    "AppliedAmount",
    "BalanceRemaining",
    "CurrencyRef",
    "ExchangeRate",
    "IsPaid",
    "IsToBePrinted",
    "IsToBeEmailed",
    "IsTaxIncluded",
    "CustomerSalesTaxCodeRef",
    "ItemSalesTaxRef",
    "LinkedTxn",
    "InvoiceLineRet",
    "InvoiceLineGroupRet",
    "DiscountLineRet",
    "SalesTaxLineRet",
    "ShippingLineRet",
)


def lookup_context(policy, payload, txn_id):
    required_id(txn_id)
    check = _check(policy, payload)
    return digest(
        {"operation": "invoice-receipt-check", "invoice": check["context_sha256"], "txn_id": txn_id}
    )


def append_lookup(discovery, correlation, txn_id):
    _request_id(correlation)
    required_id(txn_id)
    root = fromstring(discovery)
    query = ET.SubElement(root.find("QBXMLMsgsRq"), "InvoiceQueryRq", requestID=f"{correlation}3")
    ET.SubElement(query, "TxnID").text = txn_id
    ET.SubElement(query, "IncludeLineItems").text = "true"
    ET.SubElement(query, "IncludeLinkedTxns").text = "true"
    for field in RECEIPT_FIELDS:
        ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_lookup(xml, correlation, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 3:
        raise BridgeError("receipt lookup requires discovery and one invoice response")
    invoice_root = ET.Element("QBXML")
    ET.SubElement(invoice_root, "QBXMLMsgsRs").append(root[0][2])
    receipt = validate_receipt(
        ET.tostring(invoice_root), policy, payload, f"{correlation}3", txn_id=txn_id
    )
    root[0].remove(root[0][2])
    return ET.tostring(root, encoding="unicode"), receipt


def _check(policy, payload):
    check = plan(policy, payload)
    commercial = check.get("commercial")
    if (
        commercial is None
        or commercial["tax_item_id"] is not None
        or Decimal(commercial["tax_rate"]) != 0
        or check["currency_id"] is not None
        or any(s.get("kind", "Service") != "Service" for s in check["item_specs"])
    ):
        raise BridgeError(
            "receipt qualification supports only non-taxable single-currency services"
        )
    return check


def _request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,16}", value):
        raise BridgeError("invalid invoice request correlation")
    return value


def add_request(policy, payload, request_id):
    """Construct an unsubmitted request. The caller must separately qualify dispatch."""
    check = _check(policy, payload)
    invoice, masters = check["invoice"], policy.invoice_masters
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    rq = ET.SubElement(batch, "InvoiceAddRq", requestID=_request_id(request_id))
    add = ET.SubElement(rq, "InvoiceAdd")

    def ref(parent, name, value):
        ET.SubElement(ET.SubElement(parent, name), "ListID").text = value

    ref(add, "CustomerRef", masters["customers"][invoice["customer_id"]])
    ref(add, "ARAccountRef", policy.account_roles["invoice_receivable"])
    for name, value in (
        ("TxnDate", invoice["txn_date"]),
        ("RefNumber", invoice["ref_number"]),
        ("IsPending", "false"),
        ("IsFinanceCharge", "false"),
        ("IsToBePrinted", "false"),
        ("IsToBeEmailed", "false"),
    ):
        ET.SubElement(add, name).text = value
    code = check["commercial"]["sales_tax_code_id"]
    ref(add, "CustomerSalesTaxCodeRef", code)
    for line in invoice["lines"]:
        node = ET.SubElement(add, "InvoiceLineAdd")
        ref(node, "ItemRef", masters["items"][line["item_id"]]["list_id"])
        ET.SubElement(node, "Quantity").text = line["quantity"]
        ET.SubElement(node, "Rate").text = line["unit_price"]
        ref(node, "SalesTaxCodeRef", code)
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_receipt(xml, policy, payload, request_id, *, operation="InvoiceQuery", txn_id=None):
    """Require one correlated, fully matching saved invoice; absence never means success."""
    check = _check(policy, payload)
    if operation not in ("InvoiceAdd", "InvoiceQuery"):
        raise BridgeError("unsupported invoice receipt operation")
    if txn_id is not None:
        required_id(txn_id)
    try:
        root = fromstring(xml)
        if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs":
            raise BridgeError("invalid invoice receipt envelope")
        batch = root[0]
        if len(batch) != 1:
            raise BridgeError("exactly one invoice response required")
        rs = batch[0]
        if (
            rs.tag != operation + "Rs"
            or rs.get("requestID") != _request_id(request_id)
            or rs.get("statusCode") != "0"
            or rs.get("statusSeverity") != "Info"
            or len(rs) != 1
            or rs[0].tag != "InvoiceRet"
        ):
            raise BridgeError("unsuccessful, ambiguous or uncorrelated invoice receipt")
        row = rs[0]

        def value(node, path):
            # Duplicate values are never silently reduced to the first occurrence.
            current = node
            for name in path.split("/"):
                children = current.findall(name)
                if len(children) != 1:
                    raise BridgeError("missing or ambiguous invoice receipt field")
                current = children[0]
            if len(current) or current.text is None:
                raise BridgeError("malformed invoice receipt field")
            return current.text.strip()

        def equals(node, path, expected):
            if value(node, path) != expected:
                raise BridgeError("saved invoice differs from approved request")

        def number(node, path, expected):
            if decimal_evidence(value(node, path)) != Decimal(expected):
                raise BridgeError("saved invoice amount, rate or quantity differs")

        actual_id = required_id(value(row, "TxnID"))
        if txn_id is not None and actual_id != txn_id:
            raise BridgeError("saved invoice TxnID mismatch")
        edit = value(row, "EditSequence")
        if not re.fullmatch(r"[0-9]{1,16}", edit):
            raise BridgeError("invalid invoice edit sequence")
        invoice, masters = check["invoice"], policy.invoice_masters
        equals(row, "CustomerRef/ListID", masters["customers"][invoice["customer_id"]])
        equals(row, "ARAccountRef/ListID", policy.account_roles["invoice_receivable"])
        equals(row, "TxnDate", invoice["txn_date"])
        equals(row, "RefNumber", invoice["ref_number"])
        for flag in ("IsPending", "IsFinanceCharge", "IsToBePrinted", "IsToBeEmailed", "IsPaid"):
            equals(row, flag, "false")
        if any(
            row.find(tag) is not None
            for tag in (
                "InvoiceLineGroupRet",
                "DiscountLineRet",
                "ShippingLineRet",
                "SalesTaxLineRet",
                "LinkedTxn",
                "ItemSalesTaxRef",
                "CurrencyRef",
            )
        ):
            raise BridgeError("unsupported saved invoice feature")
        if row.find("ExchangeRate") is not None:
            number(row, "ExchangeRate", "1")
        if row.find("IsTaxIncluded") is not None:
            equals(row, "IsTaxIncluded", "false")
        code = check["commercial"]["sales_tax_code_id"]
        equals(row, "CustomerSalesTaxCodeRef/ListID", code)
        subtotal = sum(Decimal(line["amount"]) for line in invoice["lines"])
        number(row, "Subtotal", subtotal)
        number(row, "SalesTaxTotal", "0")
        number(row, "AppliedAmount", "0")
        number(row, "BalanceRemaining", subtotal)
        lines = row.findall("InvoiceLineRet")
        if len(lines) != len(invoice["lines"]):
            raise BridgeError("saved invoice line count differs")
        line_ids = []
        for saved, expected in zip(lines, invoice["lines"], strict=True):
            line_ids.append(required_id(value(saved, "TxnLineID")))
            equals(saved, "ItemRef/ListID", masters["items"][expected["item_id"]]["list_id"])
            equals(saved, "SalesTaxCodeRef/ListID", code)
            for field, key in (
                ("Quantity", "quantity"),
                ("Rate", "unit_price"),
                ("Amount", "amount"),
            ):
                number(saved, field, expected[key])
            if any(
                saved.find(tag) is not None
                for tag in (
                    "RatePercent",
                    "UnitOfMeasure",
                    "InventorySiteRef",
                    "InventorySiteLocationRef",
                    "SerialNumber",
                    "LotNumber",
                    "OverrideItemAccountRef",
                )
            ):
                raise BridgeError("unsupported saved invoice line feature")
        if len(set(line_ids)) != len(line_ids):
            raise BridgeError("duplicate invoice line identity")
        return {
            "txn_id": actual_id,
            "edit_sequence": edit,
            "ref_number": invoice["ref_number"],
            "subtotal": format(subtotal, ".2f"),
            "tax_amount": "0.00",
            "balance_remaining": format(subtotal, ".2f"),
            "line_ids": line_ids,
            "verification": "matched-saved-invoice",
            "scope": "receipt-only",
        }
    except BridgeError:
        raise
    except (ValueError, TypeError, AttributeError, ET.ParseError) as exc:
        raise BridgeError("invalid invoice receipt XML") from exc
