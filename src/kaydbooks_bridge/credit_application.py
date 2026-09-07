"""Apply an existing credit to one invoice; no new payment or money movement."""

import json
import re
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError, identifier, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .invoice_receipt import _request_id
from .validation import canonical, digest, money

FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Customer": ("ListID", "IsActive", "Balance", "CurrencyRef"),
    "Account": ("ListID", "IsActive", "AccountType", "CurrencyRef"),
    "Invoice": (
        "TxnID",
        "EditSequence",
        "CustomerRef",
        "ARAccountRef",
        "IsPending",
        "Subtotal",
        "SalesTaxTotal",
        "BalanceRemaining",
        "IsPaid",
        "CurrencyRef",
        "ExchangeRate",
        "LinkedTxn",
    ),
    "CreditMemo": (
        "TxnID",
        "EditSequence",
        "CustomerRef",
        "ARAccountRef",
        "IsPending",
        "TotalAmount",
        "SalesTaxTotal",
        "CreditRemaining",
        "CurrencyRef",
        "ExchangeRate",
        "LinkedTxn",
    ),
}


def validate_payload(payload, policy):
    strict_keys(
        payload,
        {
            "customer_id",
            "invoice_txn_id",
            "credit_txn_id",
            "total_amount",
            "currency",
            "ref_number",
        },
    )
    identifier(payload["customer_id"])
    if payload["customer_id"] not in policy.invoice_masters.get("customers", {}):
        raise BridgeError("credit customer mapping required")
    if "invoice_receivable" not in policy.account_roles:
        raise BridgeError("credit receivable mapping required")
    for key in ("invoice_txn_id", "credit_txn_id"):
        required_id(payload[key])
    if payload["invoice_txn_id"] == payload["credit_txn_id"]:
        raise BridgeError("credit and invoice must differ")
    if (
        payload["currency"] != policy.currency
        or policy.invoice_masters.get("currency_id") is not None
    ):
        raise BridgeError("credit application requires company base currency")
    if money(payload["total_amount"]) > money(policy.max_total):
        raise BridgeError("credit amount exceeds policy")
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,20}", payload["ref_number"]
    ):
        raise BridgeError("credit application reference invalid")
    return json.loads(canonical(payload))


def plan(policy, payload):
    payload = validate_payload(payload, policy)
    value = {
        "payload": payload,
        "customer": policy.invoice_masters["customers"][payload["customer_id"]],
        "receivable": policy.account_roles["invoice_receivable"],
    }
    return {**value, "context_sha256": digest({"schema": "credit-application-v1", **value})}


def append_check(discovery, run, check):
    _request_id(run)
    root = fromstring(discovery)
    for i, entity in enumerate(FIELDS, 3):
        q = ET.SubElement(root[0], entity + "QueryRq", requestID=run + str(i))
        if entity in ("Customer", "Account"):
            ET.SubElement(q, "ListID").text = check[
                "customer" if entity == "Customer" else "receivable"
            ]
        if entity in ("Invoice", "CreditMemo"):
            ET.SubElement(q, "TxnID").text = check["payload"][
                "invoice_txn_id" if entity == "Invoice" else "credit_txn_id"
            ]
            ET.SubElement(q, "IncludeLinkedTxns").text = "true"
        for field in FIELDS[entity]:
            ET.SubElement(q, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def links(row, other_id, other_type):
    rows = row.get("LinkedTxn", [])
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or len(rows) > 1000:
        raise BridgeError("bounded linked transaction evidence required")
    seen, result = set(), Decimal(0)
    for linked in rows:
        if not isinstance(linked, dict):
            raise BridgeError("ambiguous transaction link")
        key = required_id(linked.get("TxnID"))
        if key in seen:
            raise BridgeError("duplicate transaction link")
        seen.add(key)
        if key == other_id:
            if linked.get("TxnType") != other_type or linked.get("LinkType") != "AMTTYPE":
                raise BridgeError("credit link type differs")
            result = decimal_evidence(linked.get("Amount"))
    return result


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    responses = list(parse_response(xml))
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(responses) != 7:
        raise BridgeError("credit application response envelope differs")
    records = {}
    for i, entity in enumerate(FIELDS, 2):
        rs = responses[i]
        if (
            rs.entity != entity
            or rs.request_id != run + str(i + 1)
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("exact complete credit application evidence required")
        records[entity] = rs.records[0]
    prefs = records["Preferences"].get("MultiCurrencyPreferences")
    if not isinstance(prefs, dict) or prefs.get("IsMultiCurrencyOn") != "false":
        raise BridgeError("single-currency credit application required")
    for entity, expected in (("Customer", check["customer"]), ("Account", check["receivable"])):
        row = records[entity]
        if row.get("ListID") != expected or row.get("IsActive") != "true" or "CurrencyRef" in row:
            raise BridgeError("credit application master differs")
    if records["Account"].get("AccountType") != "AccountsReceivable":
        raise BridgeError("credit account must be receivable")
    payload = check["payload"]
    for entity, key in (("Invoice", "invoice_txn_id"), ("CreditMemo", "credit_txn_id")):
        row = records[entity]
        if (
            row.get("TxnID") != payload[key]
            or row.get("IsPending") != "false"
            or "CurrencyRef" in row
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
            or decimal_evidence(row.get("SalesTaxTotal")) != 0
        ):
            raise BridgeError("unsupported credit application transaction")
        required_id(row.get("EditSequence"))
        for field, expected in (
            ("CustomerRef", check["customer"]),
            ("ARAccountRef", check["receivable"]),
        ):
            if not isinstance(row.get(field), dict) or row[field].get("ListID") != expected:
                raise BridgeError("credit and invoice customer or AR differs")
    invoice, credit = records["Invoice"], records["CreditMemo"]
    outstanding = decimal_evidence(invoice.get("BalanceRemaining"))
    remaining = decimal_evidence(credit.get("CreditRemaining"))
    invoice_link = links(invoice, payload["credit_txn_id"], "CreditMemo")
    credit_link = links(credit, payload["invoice_txn_id"], "Invoice")
    if (
        outstanding < 0
        or remaining < 0
        or outstanding > decimal_evidence(invoice.get("Subtotal"))
        or remaining > decimal_evidence(credit.get("TotalAmount"))
    ):
        raise BridgeError("invalid credit or invoice balances")
    if invoice.get("IsPaid") != ("true" if outstanding == 0 else "false"):
        raise BridgeError("invoice paid state differs")
    if not recovering and (
        invoice_link != 0
        or credit_link != 0
        or min(outstanding, remaining) < Decimal(payload["total_amount"])
    ):
        raise BridgeError("credit pair already linked or amount exceeds available balance")
    balances = {
        "invoice": str(outstanding),
        "credit": str(remaining),
        "customer": str(decimal_evidence(records["Customer"].get("Balance"))),
        "invoice_link": str(invoice_link),
        "credit_link": str(credit_link),
    }
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), balances


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    add = ET.SubElement(
        ET.SubElement(batch, "ReceivePaymentAddRq", requestID=_request_id(run)), "ReceivePaymentAdd"
    )
    for field, key in (("CustomerRef", "customer"), ("ARAccountRef", "receivable")):
        ET.SubElement(ET.SubElement(add, field), "ListID").text = check[key]
    applied = ET.SubElement(add, "AppliedToTxnAdd")
    ET.SubElement(applied, "TxnID").text = payload["invoice_txn_id"]
    credit = ET.SubElement(applied, "SetCredit")
    ET.SubElement(credit, "CreditTxnID").text = payload["credit_txn_id"]
    ET.SubElement(credit, "AppliedAmount").text = payload["total_amount"]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def verify_effect(payload, before, after):
    if not isinstance(before, dict) or set(before) != set(after):
        raise BridgeError("original credit application baseline required")
    amount = Decimal(payload["total_amount"])
    if (
        decimal_evidence(before["invoice_link"]) != 0
        or decimal_evidence(before["credit_link"]) != 0
    ):
        raise BridgeError("original credit pair was already linked")
    expected = {
        "invoice": decimal_evidence(before["invoice"]) - amount,
        "credit": decimal_evidence(before["credit"]) - amount,
        "customer": decimal_evidence(before["customer"]),
        "invoice_link": -amount,
        "credit_link": -amount,
    }
    if any(decimal_evidence(after[k]) != value for k, value in expected.items()):
        raise BridgeError("credit application balances or reciprocal links differ; never resend")
    return {
        "kind": "credit-application",
        "new_transaction_created": False,
        "invoice_txn_id": payload["invoice_txn_id"],
        "credit_txn_id": payload["credit_txn_id"],
        "applied_amount": str(amount),
        "before": before,
        "after": after,
    }


def lookup_context(policy, payload, txn_id):
    if txn_id != payload["invoice_txn_id"]:
        raise BridgeError("credit application lookup invoice differs")
    return digest(
        {"schema": "credit-application-outcome-v1", "plan": plan(policy, payload)["context_sha256"]}
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    lookup_context(policy, payload, txn_id)
    return append_check(discovery, run, plan(policy, payload))


def validate_lookup(xml, run, policy, payload, txn_id):
    lookup_context(policy, payload, txn_id)
    discovery, balances = validate_check(xml, run, plan(policy, payload), recovering=True)
    return discovery, {
        "txn_id": txn_id,
        "kind": "credit-application",
        "new_transaction_created": False,
        "balances": balances,
    }


def validate_receipt(xml, policy, payload, run, *, operation="ReceivePaymentAdd", txn_id=None):
    """Acknowledge the link-only SDK result; independent reciprocal reads prove the outcome."""
    plan(policy, payload)
    root = fromstring(xml)
    if (
        operation != "ReceivePaymentAdd"
        or (txn_id is not None and txn_id != payload["invoice_txn_id"])
        or root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 1
    ):
        raise BridgeError("invalid credit application acknowledgement")
    rs = root[0][0]
    if (
        rs.tag != "ReceivePaymentAddRs"
        or rs.get("requestID") != run
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != "ReceivePaymentRet"
    ):
        raise BridgeError("credit application was not acknowledged")
    row = rs[0]
    if row.find("TxnID") is not None:
        raise BridgeError("unexpected new payment transaction; investigate before reconciliation")
    applied = row.findall("AppliedToTxnRet")
    if (
        len(applied) != 1
        or applied[0].findtext("TxnID") != payload["invoice_txn_id"]
        or applied[0].findtext("TxnType") != "Invoice"
    ):
        raise BridgeError("credit application acknowledgement invoice differs")
    return {
        "txn_id": payload["invoice_txn_id"],
        "kind": "credit-application",
        "new_transaction_created": False,
    }
