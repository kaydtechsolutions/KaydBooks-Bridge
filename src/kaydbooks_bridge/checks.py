"""Reviewed vendor expense checks; no bank transmission or bill settlement."""

from dataclasses import replace
from decimal import Decimal
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from . import journal_entries as journal
from .config import BridgeError, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import digest, money

FIELDS = (
    "TxnID",
    "EditSequence",
    "AccountRef",
    "PayeeEntityRef",
    "RefNumber",
    "TxnDate",
    "Amount",
    "CurrencyRef",
    "ExchangeRate",
    "Memo",
    "IsToBePrinted",
    "IsTaxIncluded",
    "SalesTaxCodeRef",
    "LinkedTxn",
    "ExpenseLineRet",
    "ItemLineRet",
    "ItemGroupLineRet",
)


def journal_view(policy, payload):
    banks = policy.supplier_payment_masters.get("banks", {})
    expenses = policy.bill_masters.get("expenses", {})
    accounts = {"bank-" + k: v for k, v in banks.items()}
    accounts.update({"expense-" + k: v for k, v in expenses.items()})
    total = sum((money(line["amount"]) for line in payload["lines"]), Decimal(0))
    entry = {
        "txn_date": payload["txn_date"],
        "ref_number": payload["ref_number"],
        "currency": payload["currency"],
        "lines": [
            {
                "account_id": "expense-" + line["expense_id"],
                "side": "debit",
                "amount": line["amount"],
                **({"memo": line["memo"]} if "memo" in line else {}),
            }
            for line in payload["lines"]
        ]
        + [
            {
                "account_id": "bank-" + payload["bank_id"],
                "side": "credit",
                "amount": format(total, ".2f"),
            }
        ],
        **({"memo": payload["memo"]} if "memo" in payload else {}),
    }
    return replace(policy, journal_masters={"accounts": accounts}), entry


def validate_payload(payload, policy):
    strict_keys(
        payload, {"bank_id", "vendor_id", "txn_date", "ref_number", "currency", "lines"}, {"memo"}
    )
    banks = policy.supplier_payment_masters.get("banks", {})
    vendors = policy.bill_masters.get("vendors", {})
    expenses = policy.bill_masters.get("expenses", {})
    if (
        not isinstance(payload["bank_id"], str)
        or payload["bank_id"] not in banks
        or not isinstance(payload["vendor_id"], str)
        or payload["vendor_id"] not in vendors
    ):
        raise BridgeError("configured check bank and vendor required")
    if not isinstance(payload["lines"], list) or not 1 <= len(payload["lines"]) <= 99:
        raise BridgeError("check requires 1-99 expense lines")
    for line in payload["lines"]:
        strict_keys(line, {"expense_id", "amount"}, {"memo"})
        if not isinstance(line["expense_id"], str) or line["expense_id"] not in expenses:
            raise BridgeError("configured check expense required")
        if expenses[line["expense_id"]] == banks[payload["bank_id"]]:
            raise BridgeError("check bank and expense accounts must differ")
    jp, entry = journal_view(policy, payload)
    normalized = journal.validate_payload(entry, jp)
    return {
        **payload,
        "lines": [
            {**line, "amount": normalized["lines"][i]["amount"]}
            for i, line in enumerate(payload["lines"])
        ],
    }


def plan(policy, payload):
    payload = validate_payload(payload, policy)
    jp, entry = journal_view(policy, payload)
    check = journal.plan(jp, entry)
    vendor = policy.bill_masters["vendors"][payload["vendor_id"]]
    bank = policy.supplier_payment_masters["banks"][payload["bank_id"]]
    return {
        "payload": payload,
        "journal": check,
        "vendor": vendor,
        "bank": bank,
        "context_sha256": digest(
            {
                "schema": "expense-check-v1",
                "journal": check,
                "vendor": vendor,
                "bank": bank,
                "payload": payload,
            }
        ),
    }


def append_check(discovery, run, check):
    root = fromstring(journal.append_check(discovery, run, check["journal"]))
    q = E.SubElement(root[0], "VendorQueryRq", requestID=run + "98")
    E.SubElement(q, "ListID").text = check["vendor"]
    for field in ("ListID", "IsActive", "CurrencyRef", "Balance"):
        E.SubElement(q, "IncludeRetElement").text = field
    return journal.render(root)


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid check master response")
    vendor = root[0][-1]
    root[0].remove(vendor)
    discovery, accounts = journal.validate_check(E.tostring(root), run, check["journal"])
    isolated = E.Element("QBXML")
    E.SubElement(isolated, "QBXMLMsgsRs").append(vendor)
    rows = list(parse_response(E.tostring(isolated)))
    if (
        vendor.tag != "VendorQueryRs"
        or vendor.get("requestID") != run + "98"
        or len(rows) != 1
        or rows[0].status_code != 0
        or rows[0].status_severity != "Info"
        or len(rows[0].records) != 1
    ):
        raise BridgeError("check vendor response is unsuccessful or uncorrelated")
    row = rows[0].records[0]
    if (
        row.get("ListID") != check["vendor"]
        or row.get("IsActive") != "true"
        or "CurrencyRef" in row
    ):
        raise BridgeError("check vendor identity/activity/currency differs")
    for key, account in accounts.items():
        allowed = (
            {"Bank"} if key == check["bank"] else {"Expense", "OtherExpense", "CostOfGoodsSold"}
        )
        if account["type"] not in allowed:
            raise BridgeError("check requires a bank and ordinary expense accounts")
    return discovery, {
        "accounts": accounts,
        "vendor_balance": str(decimal_evidence(row.get("Balance"))),
    }


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = E.Element("QBXML")
    rq = E.SubElement(
        E.SubElement(root, "QBXMLMsgsRq", onError="stopOnError"), "CheckAddRq", requestID=run
    )
    row = E.SubElement(rq, "CheckAdd")
    journal.ref(row, "AccountRef", check["bank"])
    journal.ref(row, "PayeeEntityRef", check["vendor"])
    for tag, key in (("RefNumber", "ref_number"), ("TxnDate", "txn_date"), ("Memo", "memo")):
        if key in payload:
            E.SubElement(row, tag).text = payload[key]
    E.SubElement(row, "IsToBePrinted").text = "false"
    for line in check["payload"]["lines"]:
        node = E.SubElement(row, "ExpenseLineAdd")
        journal.ref(node, "AccountRef", policy.bill_masters["expenses"][line["expense_id"]])
        E.SubElement(node, "Amount").text = line["amount"]
        if "memo" in line:
            E.SubElement(node, "Memo").text = line["memo"]
    return journal.render(root)


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("one check identity required")
    root = fromstring(discovery)
    q = E.SubElement(root[0], "CheckQueryRq", requestID=run)
    E.SubElement(q, "TxnID" if txn_id else "RefNumber").text = txn_id or ref_number
    E.SubElement(q, "IncludeLineItems").text = "true"
    E.SubElement(q, "IncludeLinkedTxns").text = "true"
    for field in FIELDS:
        E.SubElement(q, "IncludeRetElement").text = field
    return journal.render(root)


def validate_receipt(xml, policy, payload, run, *, operation="CheckQuery", txn_id=None):
    check = plan(policy, payload)
    root = fromstring(xml)
    if (
        operation not in ("CheckAdd", "CheckQuery")
        or root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 1
    ):
        raise BridgeError("exact saved check response required")
    rs = root[0][0]
    if (
        rs.tag != operation + "Rs"
        or rs.get("requestID") != run
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != "CheckRet"
    ):
        raise BridgeError("check response status/correlation differs")
    row = rs[0]
    native = required_id(journal.scalar(row, "TxnID"))
    required_id(journal.scalar(row, "EditSequence"))
    if txn_id is not None and txn_id != native:
        raise BridgeError("saved check identity differs")
    if (
        journal.reference(row, "AccountRef") != check["bank"]
        or journal.reference(row, "PayeeEntityRef") != check["vendor"]
    ):
        raise BridgeError("saved check bank/payee differs")
    for tag, key in (("TxnDate", "txn_date"), ("RefNumber", "ref_number")):
        if journal.scalar(row, tag) != payload[key]:
            raise BridgeError("saved check date/reference differs")
    if len(row.findall("Memo")) > 1 or (row.findtext("Memo") or None) != payload.get("memo"):
        raise BridgeError("saved check memo differs")
    if any(
        row.find(f) is not None
        for f in ("CurrencyRef", "SalesTaxCodeRef", "LinkedTxn", "ItemLineRet", "ItemGroupLineRet")
    ):
        raise BridgeError("unsupported saved check feature")
    for flag in ("IsToBePrinted", "IsTaxIncluded"):
        if row.find(flag) is not None and journal.scalar(row, flag) != "false":
            raise BridgeError("saved check printing/tax differs")
    if (
        row.find("ExchangeRate") is not None
        and decimal_evidence(journal.scalar(row, "ExchangeRate")) != 1
    ):
        raise BridgeError("check exchange rate differs")
    total = sum(money(line["amount"]) for line in payload["lines"])
    if money(journal.scalar(row, "Amount")) != total:
        raise BridgeError("saved check total differs")
    lines = row.findall("ExpenseLineRet")
    if len(lines) != len(payload["lines"]):
        raise BridgeError("saved check expense count differs")
    ids = []
    for node, line in zip(lines, payload["lines"], strict=True):
        ids.append(required_id(journal.scalar(node, "TxnLineID")))
        if (
            journal.reference(node, "AccountRef")
            != policy.bill_masters["expenses"][line["expense_id"]]
            or money(journal.scalar(node, "Amount")) != money(line["amount"])
            or len(node.findall("Memo")) > 1
            or (node.findtext("Memo") or None) != line.get("memo")
            or any(
                node.find(f) is not None
                for f in ("CustomerRef", "ClassRef", "TaxAmount", "SalesTaxCodeRef")
            )
        ):
            raise BridgeError("saved check expense differs")
    if len(ids) != len(set(ids)):
        raise BridgeError("ambiguous saved check line identities")
    return {
        "txn_id": native,
        "ref_number": payload["ref_number"],
        "total_amount": format(total, ".2f"),
        "line_ids": ids,
        "verification": "matched-saved-expense-check",
    }


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid check lookup envelope")
    row = root[0][-1]
    root[0].remove(row)
    discovery, balances = validate_check(
        E.tostring(root), run, plan(policy, payload), recovering=True
    )
    isolated = E.Element("QBXML")
    E.SubElement(isolated, "QBXMLMsgsRs").append(row)
    return discovery, {
        **validate_receipt(E.tostring(isolated), policy, payload, run + "99", txn_id=txn_id),
        "balances": balances,
    }


def verify_balance_effect(payload, before, after, *, policy):
    jp, entry = journal_view(policy, payload)
    effects = journal.verify_balance_effect(entry, before["accounts"], after["accounts"], policy=jp)
    if decimal_evidence(before["vendor_balance"]) != decimal_evidence(after["vendor_balance"]):
        raise BridgeError("expense check unexpectedly changed vendor payable balance; never resend")
    return {"accounts": effects, "vendor_balance_unchanged": after["vendor_balance"]}
