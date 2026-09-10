"""Apply an existing credit to one bill; no new payment or money movement."""

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
    "Vendor": ("ListID", "IsActive", "Balance", "CurrencyRef"),
    "Account": ("ListID", "IsActive", "AccountType", "Balance", "CurrencyRef"),
    "Bill": (
        "TxnID",
        "EditSequence",
        "VendorRef",
        "APAccountRef",
        "TxnDate",
        "AmountDue",
        "IsPaid",
        "IsTaxIncluded",
        "SalesTaxCodeRef",
        "CurrencyRef",
        "ExchangeRate",
        "LinkedTxn",
    ),
    "VendorCredit": (
        "TxnID",
        "EditSequence",
        "VendorRef",
        "APAccountRef",
        "TxnDate",
        "CreditAmount",
        "IsTaxIncluded",
        "SalesTaxCodeRef",
        "CurrencyRef",
        "ExchangeRate",
        "LinkedTxn",
    ),
}


def validate_payload(payload, policy):
    strict_keys(
        payload,
        {
            "vendor_id",
            "bank_id",
            "bill_txn_id",
            "credit_txn_id",
            "total_amount",
            "currency",
            "ref_number",
        },
    )
    from .supplier_payments import validate_masters

    masters = validate_masters(policy.supplier_payment_masters)
    for key, group in (("vendor_id", "vendors"), ("bank_id", "banks")):
        identifier(payload[key])
        if payload[key] not in masters.get(group, {}):
            raise BridgeError("supplier credit application mapping required")
    for key in ("bill_txn_id", "credit_txn_id"):
        required_id(payload[key])
    if payload["bill_txn_id"] == payload["credit_txn_id"]:
        raise BridgeError("bill and credit must differ")
    if payload["currency"] != policy.currency:
        raise BridgeError("supplier application requires base currency")
    if money(payload["total_amount"]) > money(policy.max_total):
        raise BridgeError("credit amount exceeds policy")
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,11}", payload["ref_number"]
    ):
        raise BridgeError(
            "supplier credit application reference requires 1-11 letters, digits or hyphens"
        )
    return json.loads(canonical(payload))


def plan(policy, payload):
    payload = validate_payload(payload, policy)
    value = {
        "payload": payload,
        "vendor": policy.supplier_payment_masters["vendors"][payload["vendor_id"]],
        "payable": policy.supplier_payment_masters["payable"],
        "bank": policy.supplier_payment_masters["banks"][payload["bank_id"]],
    }
    return {
        **value,
        "context_sha256": digest({"schema": "supplier-credit-application-v1", **value}),
    }


def append_check(discovery, run, check):
    _request_id(run)
    root = fromstring(discovery)
    for i, (entity, key) in enumerate(
        (
            ("Preferences", None),
            ("Vendor", check["vendor"]),
            ("Account", check["payable"]),
            ("Account", check["bank"]),
            ("Bill", check["payload"]["bill_txn_id"]),
            ("VendorCredit", check["payload"]["credit_txn_id"]),
        ),
        3,
    ):
        q = ET.SubElement(root[0], entity + "QueryRq", requestID=run + str(i))
        if key is not None:
            ET.SubElement(q, "TxnID" if entity in ("Bill", "VendorCredit") else "ListID").text = key
        if entity in ("Bill", "VendorCredit"):
            ET.SubElement(q, "IncludeLinkedTxns").text = "true"
        for field in FIELDS[entity]:
            ET.SubElement(q, "IncludeRetElement").text = field
    q = ET.SubElement(root[0], "BillToPayQueryRq", requestID=run + "9")
    for field, key in (("PayeeEntityRef", "vendor"), ("APAccountRef", "payable")):
        ET.SubElement(ET.SubElement(q, field), "ListID").text = check[key]
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
    from .supplier_credits import payable_rows

    root = fromstring(xml)
    responses = list(parse_response(xml))
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(responses) != 9:
        raise BridgeError("supplier application response envelope differs")
    records = []
    for i, entity in enumerate(
        ("Preferences", "Vendor", "Account", "Account", "Bill", "VendorCredit"), 2
    ):
        rs = responses[i]
        if (
            rs.entity != entity
            or rs.request_id != run + str(i + 1)
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("exact supplier application evidence required")
        records.append(rs.records[0])
    prefs, vendor, ap, bank, bill, credit = records
    if prefs.get("MultiCurrencyPreferences", {}).get("IsMultiCurrencyOn") != "false":
        raise BridgeError("single-currency application required")
    for row, key, kind in (
        (vendor, "vendor", None),
        (ap, "payable", "AccountsPayable"),
        (bank, "bank", "Bank"),
    ):
        if (
            row.get("ListID") != check[key]
            or row.get("IsActive") != "true"
            or "CurrencyRef" in row
            or (kind and row.get("AccountType") != kind)
        ):
            raise BridgeError("supplier application master differs")
    payload = check["payload"]
    for row, key in ((bill, "bill_txn_id"), (credit, "credit_txn_id")):
        if (
            row.get("TxnID") != payload[key]
            or "CurrencyRef" in row
            or "SalesTaxCodeRef" in row
            or row.get("IsTaxIncluded", "false") != "false"
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
        ):
            raise BridgeError("unsupported supplier application transaction")
        required_id(row.get("EditSequence"))
        for field, master in (("VendorRef", "vendor"), ("APAccountRef", "payable")):
            if row.get(field, {}).get("ListID") != check[master]:
                raise BridgeError("bill and credit vendor or AP differs")
    if root[0][-1].get("iteratorRemainingCount") not in (None, "0"):
        raise BridgeError("complete supplier payable evidence required")
    payables = payable_rows(responses[-1], run + "9", check["payable"])

    def amount(key, kind):
        row = payables.get(key)
        if row is None:
            if recovering:
                return Decimal(0)
            raise BridgeError("bill or unused credit absent")
        if row["kind"] != kind or row["txn_type"] != (
            "Bill" if kind == "BillToPay" else "VendorCredit"
        ):
            raise BridgeError("payable transaction type differs")
        return Decimal(row["amount"])

    outstanding = amount(payload["bill_txn_id"], "BillToPay")
    remaining = amount(payload["credit_txn_id"], "CreditToApply")
    bill_link = links(bill, payload["credit_txn_id"], "VendorCredit")
    credit_link = links(credit, payload["bill_txn_id"], "Bill")
    if (
        outstanding > decimal_evidence(bill.get("AmountDue"))
        or remaining > decimal_evidence(credit.get("CreditAmount"))
        or bill.get("IsPaid") != ("true" if outstanding == 0 else "false")
    ):
        raise BridgeError("supplier credit or bill balance invalid")
    if not recovering and (
        bill_link != 0
        or credit_link != 0
        or min(outstanding, remaining) < Decimal(payload["total_amount"])
    ):
        raise BridgeError("supplier pair already linked or balance insufficient")
    balances = {
        "bill": str(outstanding),
        "credit": str(remaining),
        "vendor": str(decimal_evidence(vendor.get("Balance"))),
        "bank": str(decimal_evidence(bank.get("Balance"))),
        "bill_link": str(bill_link),
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
        ET.SubElement(batch, "BillPaymentCheckAddRq", requestID=_request_id(run)),
        "BillPaymentCheckAdd",
    )
    for field, key in (
        ("PayeeEntityRef", "vendor"),
        ("APAccountRef", "payable"),
        ("BankAccountRef", "bank"),
    ):
        ET.SubElement(ET.SubElement(add, field), "ListID").text = check[key]
    ET.SubElement(add, "RefNumber").text = payload["ref_number"]
    applied = ET.SubElement(add, "AppliedToTxnAdd")
    ET.SubElement(applied, "TxnID").text = payload["bill_txn_id"]
    credit = ET.SubElement(applied, "SetCredit")
    ET.SubElement(credit, "CreditTxnID").text = payload["credit_txn_id"]
    ET.SubElement(credit, "AppliedAmount").text = payload["total_amount"]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def verify_effect(payload, before, after):
    if not isinstance(before, dict) or set(before) != set(after):
        raise BridgeError("original credit application baseline required")
    amount = Decimal(payload["total_amount"])
    if decimal_evidence(before["bill_link"]) != 0 or decimal_evidence(before["credit_link"]) != 0:
        raise BridgeError("original credit pair was already linked")
    expected = {
        "bill": decimal_evidence(before["bill"]) - amount,
        "credit": decimal_evidence(before["credit"]) - amount,
        "vendor": decimal_evidence(before["vendor"]),
        "bank": decimal_evidence(before["bank"]),
        "bill_link": -amount,
        "credit_link": -amount,
    }
    if any(decimal_evidence(after[k]) != value for k, value in expected.items()):
        raise BridgeError("credit application balances or reciprocal links differ; never resend")
    return {
        "kind": "supplier-credit-application",
        "cash_movement": False,
        "bill_txn_id": payload["bill_txn_id"],
        "credit_txn_id": payload["credit_txn_id"],
        "applied_amount": str(amount),
        "before": before,
        "after": after,
    }


def lookup_context(policy, payload, txn_id):
    required_id(txn_id)
    return digest(
        {
            "schema": "supplier-credit-application-outcome-v1",
            "plan": plan(policy, payload)["context_sha256"],
            "lookup_txn_id": txn_id,
        }
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    lookup_context(policy, payload, txn_id)
    request = append_check(discovery, run, plan(policy, payload))
    if txn_id != payload["bill_txn_id"]:
        from .supplier_payment_receipt import append_query

        request = append_query(request, run + "10", txn_id=txn_id)
        root = fromstring(request)
        ET.SubElement(root[0][-1], "IncludeRetElement").text = "Memo"
        request = '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(
            root, encoding="unicode"
        )
    return request


def validate_lookup(xml, run, policy, payload, txn_id):
    lookup_context(policy, payload, txn_id)
    root = fromstring(xml)
    stub = None
    if txn_id != payload["bill_txn_id"]:
        if len(root) != 1 or len(root[0]) != 10:
            raise BridgeError("exact payment stub read required")
        rs = root[0][-1]
        root[0].remove(rs)
        isolated = ET.Element("QBXML")
        ET.SubElement(isolated, "QBXMLMsgsRs").append(rs)
        stub = validate_receipt(
            ET.tostring(isolated),
            policy,
            payload,
            run + "10",
            operation="BillPaymentCheckQuery",
            txn_id=txn_id,
        )
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=True
    )
    return discovery, {
        "txn_id": txn_id,
        "bill_txn_id": payload["bill_txn_id"],
        "kind": "supplier-credit-application",
        "cash_movement": False,
        "new_transaction_created": True if stub else None,
        "payment_stub": stub,
        "balances": balances,
    }


def validate_receipt(xml, policy, payload, run, *, operation="BillPaymentCheckAdd", txn_id=None):
    """Acknowledge the link-only SDK result; independent reciprocal reads prove the outcome."""
    check = plan(policy, payload)
    root = fromstring(xml)
    if (
        operation not in ("BillPaymentCheckAdd", "BillPaymentCheckQuery")
        or root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 1
    ):
        raise BridgeError("invalid credit application acknowledgement")
    rs = root[0][0]
    if (
        rs.tag != operation + "Rs"
        or rs.get("requestID") != run
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != "BillPaymentCheckRet"
    ):
        raise BridgeError("credit application was not acknowledged")
    row = rs[0]
    for name in (
        "TxnID",
        "EditSequence",
        "PayeeEntityRef",
        "APAccountRef",
        "BankAccountRef",
        "Amount",
        "AmountInHomeCurrency",
        "IsToBePrinted",
        "Memo",
        "CurrencyRef",
        "ExchangeRate",
    ):
        if len(row.findall(name)) > 1:
            raise BridgeError("ambiguous payment stub fields")
    actual = row.findtext("TxnID")
    if actual is not None:
        required_id(actual)
        required_id(row.findtext("EditSequence"))
        if txn_id is not None and actual != txn_id:
            raise BridgeError("payment stub identity differs")
        if (
            row.findtext("Memo")
            != "QuickBooks generated zero amount transaction for bill payment stub"
        ):
            raise BridgeError(
                "unexpected new payment transaction; investigate before reconciliation"
            )
        if (
            actual in (payload["bill_txn_id"], payload["credit_txn_id"])
            or row.findtext("IsToBePrinted") != "false"
        ):
            raise BridgeError("payment stub identity or printing differs")
        for name in ("Amount", "AmountInHomeCurrency"):
            if decimal_evidence(row.findtext(name, "0")) != 0:
                raise BridgeError("payment stub has monetary amount")
        if (
            row.find("CurrencyRef") is not None
            or decimal_evidence(row.findtext("ExchangeRate", "1")) != 1
        ):
            raise BridgeError("payment stub currency differs")
        for field, key in (
            ("PayeeEntityRef", "vendor"),
            ("APAccountRef", "payable"),
            ("BankAccountRef", "bank"),
        ):
            if row.findtext(field + "/ListID") != check[key]:
                raise BridgeError("payment stub account or vendor differs")
    elif operation == "BillPaymentCheckQuery" or txn_id not in (None, payload["bill_txn_id"]):
        raise BridgeError("exact saved payment stub required")
    applied = row.findall("AppliedToTxnRet")
    if not (operation == "BillPaymentCheckQuery" and actual and not applied) and (
        len(applied) != 1
        or applied[0].findtext("TxnID") != payload["bill_txn_id"]
        or applied[0].findtext("TxnType") != "Bill"
    ):
        raise BridgeError("credit application acknowledgement bill differs")
    if applied and any(
        decimal_evidence(applied[0].findtext(name, "0")) != 0
        for name in ("PaymentAmount", "DiscountAmount")
    ):
        raise BridgeError("credit-only application cannot contain a payment or discount")
    return {
        "txn_id": actual or payload["bill_txn_id"],
        "bill_txn_id": payload["bill_txn_id"],
        "kind": "supplier-credit-application",
        "new_transaction_created": actual is not None,
        "cash_movement": False,
        "zero_amount_stub": actual is not None,
    }
