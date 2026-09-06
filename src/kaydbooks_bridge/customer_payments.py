"""Exact customer-payment allocation checks; this module cannot dispatch writes."""

import json
import re
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError, identifier, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import canonical, digest, money

FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Customer": ("ListID", "Name", "IsActive", "CurrencyRef"),
    "Account": (
        "ListID",
        "FullName",
        "IsActive",
        "AccountType",
        "SpecialAccountType",
        "CurrencyRef",
    ),
    "PaymentMethod": ("ListID", "Name", "IsActive", "PaymentMethodType"),
    "Invoice": (
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
        "IsPaid",
        "CurrencyRef",
        "ExchangeRate",
    ),
}


def append_methods(discovery, run):
    root = fromstring(discovery)
    query = ET.SubElement(root[0], "PaymentMethodQueryRq", requestID=f"{run}3")
    ET.SubElement(query, "MaxReturned").text = "20"
    ET.SubElement(query, "ActiveStatus").text = "ActiveOnly"
    for field in FIELDS["PaymentMethod"]:
        ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_methods(xml, run):
    responses = list(parse_response(xml))
    if len(responses) != 3:
        raise BridgeError("payment method preview response count differs")
    rs = responses[2]
    if (
        rs.entity != "PaymentMethod"
        or rs.request_id != f"{run}3"
        or rs.status_code != 0
        or rs.status_severity != "Info"
        or len(rs.records) > 20
    ):
        raise BridgeError("payment method preview response invalid")
    seen = set()
    for row in rs.records:
        key = required_id(row.get("ListID"))
        if key in seen or row.get("IsActive") != "true" or not isinstance(row.get("Name"), str):
            raise BridgeError("invalid payment method record")
        seen.add(key)
    root = fromstring(xml)
    root[0].remove(root[0][2])
    return ET.tostring(root, encoding="unicode"), {"PaymentMethod": rs.records}


def validate_masters(value):
    if value == {}:
        return {}
    strict_keys(value, {"customers", "receivable", "deposits", "methods"}, {"allow_unapplied"})
    required_id(value["receivable"])
    for group in ("customers", "deposits", "methods"):
        if not isinstance(value[group], dict) or not 1 <= len(value[group]) <= 1000:
            raise BridgeError("bounded payment master mappings required")
        for alias, list_id in value[group].items():
            identifier(alias)
            required_id(list_id)
    if value["receivable"] in value["deposits"].values():
        raise BridgeError("receivable and payment deposit accounts must differ")
    if type(value.get("allow_unapplied", False)) is not bool:
        raise BridgeError("allow_unapplied must be boolean")
    return json.loads(canonical(value))


def validate_payload(payload, policy):
    strict_keys(
        payload,
        {
            "customer_id",
            "deposit_id",
            "method_id",
            "txn_date",
            "ref_number",
            "currency",
            "total_amount",
            "allocations",
        },
    )
    masters = validate_masters(policy.payment_masters)
    if not masters:
        raise BridgeError("customer payment mappings are not configured")
    for key, group in (
        ("customer_id", "customers"),
        ("deposit_id", "deposits"),
        ("method_id", "methods"),
    ):
        identifier(payload[key])
        if payload[key] not in masters[group]:
            raise BridgeError("payment master is outside the company allowlist")
    if payload["currency"] != policy.currency:
        raise BridgeError("payment currency differs from company base currency")
    if not isinstance(payload["txn_date"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", payload["txn_date"]
    ):
        raise BridgeError("payment date must be YYYY-MM-DD")
    try:
        date.fromisoformat(payload["txn_date"])
    except ValueError as exc:
        raise BridgeError("invalid payment date") from exc
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,20}", payload["ref_number"]
    ):
        raise BridgeError("payment reference requires 1-20 letters, digits or hyphens")
    total = money(payload["total_amount"])
    if total > money(policy.max_total):
        raise BridgeError("company payment total limit exceeded")
    allocations = payload["allocations"]
    if not isinstance(allocations, list) or not 0 <= len(allocations) <= 20:
        raise BridgeError("payment accepts at most 20 explicit allocations")
    seen, applied = set(), Decimal("0")
    for allocation in allocations:
        strict_keys(allocation, {"txn_id", "amount"})
        txn_id = required_id(allocation["txn_id"])
        if txn_id in seen:
            raise BridgeError("duplicate payment invoice allocation")
        seen.add(txn_id)
        applied += money(allocation["amount"])
    if applied > total or (applied != total and not masters.get("allow_unapplied", False)):
        raise BridgeError(
            "unapplied payment requires explicit company policy; allocations cannot exceed total"
        )
    return json.loads(canonical(payload))


def plan(policy, payload):
    payment = validate_payload(payload, policy)
    masters = policy.payment_masters
    binding = {
        "customer": masters["customers"][payment["customer_id"]],
        "receivable": masters["receivable"],
        "deposit": masters["deposits"][payment["deposit_id"]],
        "method": masters["methods"][payment["method_id"]],
    }
    queries = [
        ("Preferences", None),
        ("Customer", binding["customer"]),
        ("Account", binding["receivable"]),
        ("Account", binding["deposit"]),
        ("PaymentMethod", binding["method"]),
    ]
    queries.extend(("Invoice", a["txn_id"]) for a in payment["allocations"])
    return {
        "payment": payment,
        "binding": binding,
        "queries": queries,
        "context_sha256": digest(
            {
                "schema": "customer-payment-check-v1",
                "payment": payment,
                "masters": masters,
                "currency": policy.currency,
                "max_total": policy.max_total,
            }
        ),
    }


def append_check(discovery, run, check):
    if not isinstance(run, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", run):
        raise BridgeError("invalid payment check correlation")
    root = fromstring(discovery)
    for i, (entity, key) in enumerate(check["queries"], 3):
        query = ET.SubElement(root[0], entity + "QueryRq", requestID=f"{run}{i}")
        if key is not None:
            ET.SubElement(query, "TxnID" if entity == "Invoice" else "ListID").text = key
        for field in FIELDS[entity]:
            ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def invoice_balance(record, binding):
    """Current exact invoice identity and internally consistent outstanding balance."""
    for field, key in (("CustomerRef", "customer"), ("ARAccountRef", "receivable")):
        ref = record.get(field)
        if not isinstance(ref, dict) or ref.get("ListID") != binding[key]:
            raise BridgeError("payment invoice customer or receivable differs")
    if record.get("IsPending") != "false" or record.get("IsFinanceCharge") != "false":
        raise BridgeError("unsupported pending or finance-charge invoice")
    if "CurrencyRef" in record or decimal_evidence(record.get("ExchangeRate", "1")) != 1:
        raise BridgeError("payment invoice must be single-currency")
    if not isinstance(record.get("EditSequence"), str) or not re.fullmatch(
        r"[0-9]{1,16}", record["EditSequence"]
    ):
        raise BridgeError("payment invoice edit sequence missing")
    txn_date = record.get("TxnDate")
    try:
        if not isinstance(txn_date, str) or date.fromisoformat(txn_date).isoformat() != txn_date:
            raise ValueError()
    except ValueError as exc:
        raise BridgeError("payment invoice date invalid") from exc
    total = decimal_evidence(record.get("Subtotal")) + decimal_evidence(record.get("SalesTaxTotal"))
    signed_applied = decimal_evidence(record.get("AppliedAmount"))
    # InvoiceRet uses a signed reduction: 15 + (-5) = 10 still outstanding.
    applied = -signed_applied
    balance = decimal_evidence(record.get("BalanceRemaining"))
    if total < 0 or applied < 0 or balance < 0 or total != applied + balance:
        raise BridgeError("payment invoice balance equation differs")
    if record.get("IsPaid") != ("true" if balance == 0 else "false"):
        raise BridgeError("payment invoice paid status differs")
    return {
        "txn_id": required_id(record["TxnID"]),
        "edit_sequence": record["EditSequence"],
        "txn_date": txn_date,
        "ref_number": record.get("RefNumber"),
        "total": str(total),
        "applied": str(applied),
        "applied_amount_observed": str(signed_applied),
        "balance": str(balance),
    }


def validate_check(xml, run, check, *, recovering=False):
    responses = list(parse_response(xml))
    expected = [("Host", None), ("Company", None)] + check["queries"]
    if len(responses) != len(expected):
        raise BridgeError("payment evidence response set differs")
    rows = []
    for i, (rs, (entity, key)) in enumerate(zip(responses, expected, strict=True), 1):
        if (
            rs.entity != entity
            or rs.request_id != f"{run}{i}"
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("payment evidence status, identity or correlation differs")
        row = rs.records[0]
        if key is not None and row.get("TxnID" if entity == "Invoice" else "ListID") != key:
            raise BridgeError("payment evidence selector mismatch")
        if key is not None and entity != "Invoice" and row.get("IsActive") != "true":
            raise BridgeError("inactive payment master")
        rows.append(row)
    prefs = rows[2].get("MultiCurrencyPreferences")
    if (
        not isinstance(prefs, dict)
        or prefs.get("IsMultiCurrencyOn") != "false"
        or "HomeCurrencyRef" in prefs
        or any("CurrencyRef" in row for row in rows[3:])
    ):
        raise BridgeError("payments currently require single-currency QuickBooks")
    if rows[4].get("AccountType") != "AccountsReceivable":
        raise BridgeError("payment receivable account type differs")
    deposit = rows[5]
    if deposit.get("AccountType") != "Bank" and not (
        deposit.get("AccountType") == "OtherCurrentAsset"
        and deposit.get("SpecialAccountType") == "UndepositedFunds"
    ):
        raise BridgeError("payment deposit must be Bank or verified UndepositedFunds")
    if rows[6].get("PaymentMethodType") not in ("Cash", "Check"):
        raise BridgeError("payment qualification supports cash/check accounting methods only")
    balances = {}
    for row, allocation in zip(rows[7:], check["payment"]["allocations"], strict=True):
        balance = invoice_balance(row, check["binding"])
        if balance["txn_date"] > check["payment"]["txn_date"]:
            raise BridgeError("payment precedes allocated invoice")
        if not recovering and money(allocation["amount"]) > Decimal(balance["balance"]):
            raise BridgeError("payment allocation exceeds current invoice balance")
        balances[allocation["txn_id"]] = balance
    root = fromstring(xml)
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), balances
