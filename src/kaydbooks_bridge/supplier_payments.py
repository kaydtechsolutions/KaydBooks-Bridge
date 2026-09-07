"""Fixed vendor/bill payment checks. No raw XML or money-transfer interface."""

import json
import re
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from . import settlement_discounts as discounts
from .config import BridgeError, identifier, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import canonical, digest, money

FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Vendor": ("ListID", "Name", "IsActive", "CurrencyRef", "Balance"),
    "Account": ("ListID", "FullName", "IsActive", "AccountType", "CurrencyRef"),
    "Bill": (
        "TxnID",
        "EditSequence",
        "VendorRef",
        "APAccountRef",
        "TxnDate",
        "DueDate",
        "RefNumber",
        "AmountDue",
        "OpenAmount",
        "IsPaid",
        "CurrencyRef",
        "ExchangeRate",
    ),
}


def validate_masters(value):
    if value == {}:
        return {}
    strict_keys(value, {"vendors", "payable", "banks"})
    required_id(value["payable"])
    for group in ("vendors", "banks"):
        if not isinstance(value[group], dict) or not 1 <= len(value[group]) <= 1000:
            raise BridgeError("bounded supplier payment mappings required")
        for alias, key in value[group].items():
            identifier(alias)
            required_id(key)
    if value["payable"] in value["banks"].values():
        raise BridgeError("payable and bank accounts must differ")
    return json.loads(canonical(value))


def validate_payload(payload, policy, *, recovering=False):
    strict_keys(
        payload,
        {
            "vendor_id",
            "bank_id",
            "txn_date",
            "ref_number",
            "currency",
            "total_amount",
            "allocations",
        },
    )
    masters = validate_masters(policy.supplier_payment_masters)
    if not masters:
        raise BridgeError("supplier payment mappings are not configured")
    for key, group in (("vendor_id", "vendors"), ("bank_id", "banks")):
        identifier(payload[key])
        if payload[key] not in masters[group]:
            raise BridgeError("supplier payment master is outside the company allowlist")
    if payload["currency"] != policy.currency:
        raise BridgeError("supplier payment currency differs")
    try:
        if (
            not isinstance(payload["txn_date"], str)
            or date.fromisoformat(payload["txn_date"]).isoformat() != payload["txn_date"]
        ):
            raise ValueError()
    except ValueError as exc:
        raise BridgeError("supplier payment date must be YYYY-MM-DD") from exc
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,20}" if recovering else r"[A-Za-z0-9-]{1,11}", payload["ref_number"]
    ):
        raise BridgeError("supplier payment reference requires 1-11 letters, digits or hyphens")
    total = money(payload["total_amount"])
    if total > money(policy.max_total):
        raise BridgeError("company payment limit exceeded")
    allocations = payload["allocations"]
    if not isinstance(allocations, list) or not 1 <= len(allocations) <= 20:
        raise BridgeError("supplier payment requires 1-20 explicit bill allocations")
    seen, applied = set(), Decimal(0)
    for allocation in allocations:
        discounts.validate(allocation, policy, "supplier")
        key = required_id(allocation["txn_id"])
        if key in seen:
            raise BridgeError("duplicate supplier bill allocation")
        seen.add(key)
        applied += money(allocation["amount"])
    if applied != total:
        raise BridgeError("supplier payment allocations must equal total")
    if total + sum(discounts.amount(a) for a in allocations) > money(policy.max_total):
        raise BridgeError("cash plus settlement discounts exceed company limit")
    return json.loads(canonical(payload))


def plan(policy, payload, *, recovering=False):
    # The old 20-character bound is retained only for reading a dispatched legacy request.
    payment = validate_payload(payload, policy, recovering=recovering)
    masters = policy.supplier_payment_masters
    binding = {
        "vendor": masters["vendors"][payment["vendor_id"]],
        "payable": masters["payable"],
        "bank": masters["banks"][payment["bank_id"]],
        **discounts.binding(policy, payment, "supplier"),
    }
    queries = [
        ("Preferences", None),
        ("Vendor", binding["vendor"]),
        ("Account", binding["payable"]),
        ("Account", binding["bank"]),
    ]
    queries.extend(("Bill", a["txn_id"]) for a in payment["allocations"])
    if "discount_account" in binding:
        queries.append(("Account", binding["discount_account"]))
    return {
        "payment": payment,
        "binding": binding,
        "queries": queries,
        "context_sha256": digest(
            {
                "schema": "supplier-payment-check-v1",
                "payment": payment,
                "masters": masters,
                "currency": policy.currency,
                "max_total": policy.max_total,
                **discounts.binding(policy, payment, "supplier"),
            }
        ),
    }


def append_check(discovery, run, check):
    if not isinstance(run, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", run):
        raise BridgeError("invalid supplier payment correlation")
    root = fromstring(discovery)
    for i, (entity, key) in enumerate(check["queries"], 3):
        query = ET.SubElement(root[0], entity + "QueryRq", requestID=f"{run}{i}")
        if key is not None:
            ET.SubElement(query, "TxnID" if entity == "Bill" else "ListID").text = key
        for field in FIELDS[entity]:
            ET.SubElement(query, "IncludeRetElement").text = field
    payable = ET.SubElement(root[0], "BillToPayQueryRq", requestID=run + "90")
    for field, key in (("PayeeEntityRef", "vendor"), ("APAccountRef", "payable")):
        ET.SubElement(ET.SubElement(payable, field), "ListID").text = check["binding"][key]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def payables(rs, run, binding):
    if (
        rs.tag != "BillToPayQueryRs"
        or rs.get("requestID") != run + "90"
        or rs.get("iteratorRemainingCount") not in (None, "0")
        or len(rs) > 1000
    ):
        raise BridgeError("incomplete supplier payable response")
    status = (rs.get("statusCode"), rs.get("statusSeverity"))
    if status != ("0", "Info") and not (
        len(rs) == 0 and status in (("1", "Info"), ("500", "Warn"))
    ):
        raise BridgeError("unsuccessful supplier payable response")
    values, seen = {}, set()
    for entry in rs:
        if (
            entry.tag != "BillToPayRet"
            or len(entry) != 1
            or entry[0].tag not in ("BillToPay", "CreditToApply")
        ):
            raise BridgeError("ambiguous supplier payable entry")
        row = entry[0]
        for field in (
            "TxnID",
            "TxnType",
            "APAccountRef",
            "TxnDate",
            "DueDate",
            "RefNumber",
            "AmountDue",
            "ExchangeRate",
            "AmountDueInHomeCurrency",
        ):
            if len(row.findall(field)) > 1:
                raise BridgeError("ambiguous supplier payable field")
        key = required_id(row.findtext("TxnID"))
        if key in seen:
            raise BridgeError("duplicate supplier payable entry")
        seen.add(key)
        if row.tag == "CreditToApply":
            continue  # No credit application is permitted by the write contract.
        if (
            row.findtext("TxnType") != "Bill"
            or row.findtext("APAccountRef/ListID") != binding["payable"]
            or row.find("CurrencyRef") is not None
            or decimal_evidence(row.findtext("ExchangeRate", "1")) != 1
        ):
            raise BridgeError("supplier payable identity or currency differs")
        amount = decimal_evidence(row.findtext("AmountDue"))
        if amount < 0 or (
            row.find("AmountDueInHomeCurrency") is not None
            and decimal_evidence(row.findtext("AmountDueInHomeCurrency")) != amount
        ):
            raise BridgeError("supplier payable amount differs")
        values[key] = {
            "balance": str(amount),
            **{k: row.findtext(k) for k in ("TxnDate", "DueDate", "RefNumber")},
        }
    return values


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    if len(root) != 1 or len(root[0]) < 8:
        raise BridgeError("supplier payment response set differs")
    payable = root[0][-1]
    root[0].remove(payable)
    available = payables(payable, run, check["binding"])
    responses = list(parse_response(ET.tostring(root)))
    expected = [("Host", None), ("Company", None)] + check["queries"]
    if len(responses) != len(expected):
        raise BridgeError("supplier payment response count differs")
    rows = []
    for i, (rs, (entity, key)) in enumerate(zip(responses, expected, strict=True), 1):
        if (
            rs.entity != entity
            or rs.request_id != f"{run}{i}"
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("supplier payment status, identity or correlation differs")
        row = rs.records[0]
        if key is not None and row.get("TxnID" if entity == "Bill" else "ListID") != key:
            raise BridgeError("supplier payment selector differs")
        if key is not None and entity != "Bill" and row.get("IsActive") != "true":
            raise BridgeError("inactive supplier payment master")
        rows.append(row)
    prefs = rows[2].get("MultiCurrencyPreferences")
    if (
        not isinstance(prefs, dict)
        or prefs.get("IsMultiCurrencyOn") != "false"
        or "HomeCurrencyRef" in prefs
        or any("CurrencyRef" in row for row in rows[3:])
    ):
        raise BridgeError("supplier payments currently require single-currency QuickBooks")
    if rows[4].get("AccountType") != "AccountsPayable" or rows[5].get("AccountType") != "Bank":
        raise BridgeError("supplier payment requires payable and bank account types")
    discounts.check_account(rows, check, "supplier")
    balances = {}
    for row, allocation in zip(rows[6:], check["payment"]["allocations"], strict=True):
        for field, key in (("VendorRef", "vendor"), ("APAccountRef", "payable")):
            if (
                not isinstance(row.get(field), dict)
                or row[field].get("ListID") != check["binding"][key]
            ):
                raise BridgeError("allocated bill vendor or payable differs")
        if (
            not isinstance(row.get("EditSequence"), str)
            or not re.fullmatch(r"[0-9]{1,16}", row["EditSequence"])
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
        ):
            raise BridgeError("allocated bill version or currency invalid")
        for key in ("TxnDate", "DueDate"):
            try:
                if date.fromisoformat(row[key]).isoformat() != row[key]:
                    raise ValueError()
            except (KeyError, TypeError, ValueError) as exc:
                raise BridgeError("allocated bill date invalid") from exc
        if row["TxnDate"] > check["payment"]["txn_date"]:
            raise BridgeError("supplier payment precedes bill")
        total = decimal_evidence(row.get("AmountDue"))
        due = available.get(allocation["txn_id"])
        if due is None:
            if not recovering or row.get("IsPaid") != "true":
                raise BridgeError("exact unpaid bill missing from complete payable query")
            balance = Decimal("0.00")
        else:
            if any(due[k] != row.get(k) for k in ("TxnDate", "DueDate", "RefNumber")):
                raise BridgeError("allocated bill payable identity differs")
            balance = Decimal(due["balance"])
        if (
            total <= 0
            or not 0 <= balance <= total
            or row.get("IsPaid") != ("true" if balance == 0 else "false")
        ):
            raise BridgeError("allocated bill paid state or amount differs")
        if not recovering and discounts.settled(allocation) > balance:
            raise BridgeError("supplier payment exceeds bill balance")
        balances[allocation["txn_id"]] = {
            "txn_id": allocation["txn_id"],
            "edit_sequence": row["EditSequence"],
            "txn_date": row["TxnDate"],
            "due_date": row["DueDate"],
            "ref_number": row.get("RefNumber"),
            "total": str(total),
            "balance": str(balance),
            "applied": str(total - balance),
            "open_amount_observed": str(decimal_evidence(row.get("OpenAmount", "0"))),
        }
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), balances
