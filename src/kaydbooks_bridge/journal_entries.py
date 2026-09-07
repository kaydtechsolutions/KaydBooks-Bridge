"""Balanced non-tax journal entries with independent account-balance readback."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError, identifier, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import digest, money

DEBIT_NORMAL = {
    "Bank",
    "AccountsReceivable",
    "OtherCurrentAsset",
    "FixedAsset",
    "OtherAsset",
    "Expense",
    "OtherExpense",
    "CostOfGoodsSold",
}
ALLOWED_TYPES = (
    DEBIT_NORMAL
    | {
        "CreditCard",
        "OtherCurrentLiability",
        "LongTermLiability",
        "Equity",
        "Income",
        "OtherIncome",
    }
) - {"AccountsReceivable"}
FIELDS = (
    "TxnID",
    "EditSequence",
    "TxnDate",
    "RefNumber",
    "Memo",
    "IsAdjustment",
    "IsHomeCurrencyAdjustment",
    "IsAmountsEnteredInHomeCurrency",
    "CurrencyRef",
    "ExchangeRate",
    "JournalDebitLine",
    "JournalCreditLine",
)


def validate_masters(value):
    if value == {}:
        return {}
    strict_keys(value, {"accounts"})
    if not isinstance(value["accounts"], dict) or not 2 <= len(value["accounts"]) <= 1000:
        raise BridgeError("configure 2-1000 journal account aliases")
    for alias, native in value["accounts"].items():
        identifier(alias)
        required_id(native)
    return value


def memo(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4095 or any(ord(c) < 32 for c in value):
        raise BridgeError("journal memo must be 1-4095 printable characters")
    return value


def validate_payload(payload, policy):
    strict_keys(payload, {"txn_date", "ref_number", "currency", "lines"}, {"memo"})
    maps = validate_masters(policy.journal_masters)
    if not maps:
        raise BridgeError("journal accounts are not configured")
    if payload["currency"] != policy.currency:
        raise BridgeError("journal currency differs from company")
    try:
        if date.fromisoformat(payload["txn_date"]).isoformat() != payload["txn_date"]:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise BridgeError("journal date requires YYYY-MM-DD") from exc
    ref = payload["ref_number"]
    if (
        not isinstance(ref, str)
        or not 1 <= len(ref) <= 11
        or not ref.isascii()
        or not all(c.isalnum() or c == "-" for c in ref)
    ):
        raise BridgeError("journal reference requires 1-11 ASCII letters, digits or hyphens")
    lines = payload["lines"]
    if not isinstance(lines, list) or not 2 <= len(lines) <= 100:
        raise BridgeError("journal requires 2-100 debit/credit lines")
    normalized = []
    totals = {"debit": Decimal(0), "credit": Decimal(0)}
    accounts = set()
    for line in lines:
        strict_keys(line, {"account_id", "side", "amount"}, {"memo"})
        identifier(line["account_id"])
        if line["account_id"] not in maps["accounts"] or line["side"] not in totals:
            raise BridgeError("configured journal account and debit/credit side required")
        amount = money(line["amount"])
        if amount <= 0:
            raise BridgeError("journal amounts must be positive")
        totals[line["side"]] += amount
        accounts.add(maps["accounts"][line["account_id"]])
        normalized.append(
            {
                **line,
                "amount": format(amount, ".2f"),
                **({"memo": memo(line["memo"])} if "memo" in line else {}),
            }
        )
    if (
        len(accounts) < 2
        or totals["debit"] != totals["credit"]
        or totals["debit"] > money(policy.max_total)
    ):
        raise BridgeError("journal must balance within company limit across distinct accounts")
    return {
        **payload,
        "lines": normalized,
        **({"memo": memo(payload["memo"])} if "memo" in payload else {}),
    }


def plan(policy, payload):
    entry = validate_payload(payload, policy)
    accounts = {
        alias: policy.journal_masters["accounts"][alias]
        for alias in sorted({line["account_id"] for line in entry["lines"]})
    }
    return {
        "entry": entry,
        "accounts": accounts,
        "context_sha256": digest(
            {
                "schema": "journal-v1",
                "entry": entry,
                "accounts": accounts,
                "currency": policy.currency,
            }
        ),
    }


def render(root):
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + E.tostring(root, encoding="unicode")


def ref(parent, tag, key):
    E.SubElement(E.SubElement(parent, tag), "ListID").text = key


def append_check(discovery, run, check):
    root = fromstring(discovery)
    q = E.SubElement(root[0], "PreferencesQueryRq", requestID=run + "3")
    E.SubElement(q, "IncludeRetElement").text = "MultiCurrencyPreferences"
    for i, key in enumerate(sorted(set(check["accounts"].values())), 4):
        q = E.SubElement(root[0], "AccountQueryRq", requestID=run + str(i))
        E.SubElement(q, "ListID").text = key
        for field in (
            "ListID",
            "IsActive",
            "AccountType",
            "SpecialAccountType",
            "CurrencyRef",
            "Balance",
        ):
            E.SubElement(q, "IncludeRetElement").text = field
    return render(root)


def validate_check(xml, run, check, *, recovering=False):
    keys = sorted(set(check["accounts"].values()))
    root = fromstring(xml)
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 3 + len(keys)
    ):
        raise BridgeError("journal master response set differs")
    rows = list(parse_response(xml))
    prefs = rows[2]
    if (
        prefs.entity != "Preferences"
        or prefs.request_id != run + "3"
        or prefs.status_code != 0
        or prefs.status_severity != "Info"
        or len(prefs.records) != 1
        or prefs.records[0].get("MultiCurrencyPreferences", {}).get("IsMultiCurrencyOn") != "false"
    ):
        raise BridgeError("journal requires verified single-currency preferences")
    balances = {}
    for i, (rs, key) in enumerate(zip(rows[3:], keys, strict=True), 4):
        if (
            rs.entity != "Account"
            or rs.request_id != run + str(i)
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("journal account response is unsuccessful or uncorrelated")
        row = rs.records[0]
        if (
            row.get("ListID") != key
            or row.get("IsActive") != "true"
            or row.get("AccountType") not in ALLOWED_TYPES
            or "CurrencyRef" in row
            or "SpecialAccountType" in row
        ):
            raise BridgeError("journal account identity/type/activity is unsupported")
        balances[key] = {
            "balance": str(decimal_evidence(row.get("Balance"))),
            "type": row["AccountType"],
        }
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return E.tostring(root, encoding="unicode"), balances


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = E.Element("QBXML")
    rq = E.SubElement(
        E.SubElement(root, "QBXMLMsgsRq", onError="stopOnError"), "JournalEntryAddRq", requestID=run
    )
    row = E.SubElement(rq, "JournalEntryAdd")
    for tag, key in [("TxnDate", "txn_date"), ("RefNumber", "ref_number")]:
        if key in payload:
            E.SubElement(row, tag).text = payload[key]
    E.SubElement(row, "IsAdjustment").text = "false"
    for line in check["entry"]["lines"]:
        node = E.SubElement(
            row, "JournalDebitLine" if line["side"] == "debit" else "JournalCreditLine"
        )
        ref(node, "AccountRef", check["accounts"][line["account_id"]])
        E.SubElement(node, "Amount").text = line["amount"]
        line_memo = line.get("memo", payload.get("memo"))
        if line_memo is not None:
            E.SubElement(node, "Memo").text = line_memo
    return render(root)


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("one journal identity required")
    root = fromstring(discovery)
    q = E.SubElement(root[0], "JournalEntryQueryRq", requestID=run)
    E.SubElement(q, "TxnID" if txn_id else "RefNumber").text = txn_id or ref_number
    E.SubElement(q, "IncludeLineItems").text = "true"
    for field in FIELDS:
        E.SubElement(q, "IncludeRetElement").text = field
    return render(root)


def scalar(row, tag):
    nodes = row.findall(tag)
    if len(nodes) != 1 or len(nodes[0]) or nodes[0].text is None:
        raise BridgeError("missing or ambiguous journal field")
    return nodes[0].text


def reference(row, tag):
    nodes = row.findall(tag)
    if len(nodes) != 1:
        raise BridgeError("ambiguous journal reference")
    return scalar(nodes[0], "ListID")


def validate_receipt(xml, policy, payload, run, *, operation="JournalEntryQuery", txn_id=None):
    check = plan(policy, payload)
    root = fromstring(xml)
    if (
        operation not in ("JournalEntryAdd", "JournalEntryQuery")
        or root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 1
    ):
        raise BridgeError("exact saved journal response required")
    rs = root[0][0]
    if (
        rs.tag != operation + "Rs"
        or rs.get("requestID") != run
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != "JournalEntryRet"
    ):
        raise BridgeError("journal status/correlation differs")
    row = rs[0]
    native = required_id(scalar(row, "TxnID"))
    required_id(scalar(row, "EditSequence"))
    if txn_id is not None and txn_id != native:
        raise BridgeError("saved journal identity differs")
    for field, key in [("TxnDate", "txn_date"), ("RefNumber", "ref_number")]:
        if scalar(row, field) != payload[key]:
            raise BridgeError("saved journal header differs")
    if (
        len(row.findall("Memo")) > 1
        or row.find("Memo") is not None
        or row.find("CurrencyRef") is not None
    ):
        raise BridgeError("saved journal memo/currency differs")
    for flag in ("IsAdjustment", "IsHomeCurrencyAdjustment", "IsAmountsEnteredInHomeCurrency"):
        if row.find(flag) is not None and scalar(row, flag) != "false":
            raise BridgeError("unsupported journal adjustment")
    if row.find("ExchangeRate") is not None and decimal_evidence(scalar(row, "ExchangeRate")) != 1:
        raise BridgeError("journal exchange rate differs")
    lines = [n for n in row if n.tag in ("JournalDebitLine", "JournalCreditLine")]
    if len(lines) != len(payload["lines"]):
        raise BridgeError("saved journal line count differs")
    # QuickBooks may group debit and credit lines; compare exact multisets, preserving duplicates.
    observed = []
    ids = []
    for line in lines:
        ids.append(required_id(scalar(line, "TxnLineID")))
        if any(
            line.find(f) is not None
            for f in ("EntityRef", "ClassRef", "ItemSalesTaxRef", "TaxAmount")
        ):
            raise BridgeError("unsupported saved journal line feature")
        if len(line.findall("Memo")) > 1:
            raise BridgeError("ambiguous journal line memo")
        observed.append(
            (
                line.tag,
                reference(line, "AccountRef"),
                str(money(scalar(line, "Amount"))),
                line.findtext("Memo") or "",
            )
        )
    expected = [
        (
            "JournalDebitLine" if line["side"] == "debit" else "JournalCreditLine",
            check["accounts"][line["account_id"]],
            str(money(line["amount"])),
            line.get("memo", payload.get("memo", "")),
        )
        for line in payload["lines"]
    ]
    if len(set(ids)) != len(ids) or sorted(observed) != sorted(expected):
        raise BridgeError("saved journal lines differ")
    total = sum(money(line["amount"]) for line in payload["lines"] if line["side"] == "debit")
    return {
        "txn_id": native,
        "ref_number": payload["ref_number"],
        "total_amount": format(total, ".2f"),
        "verification": "matched-saved-journal",
        "line_ids": ids,
    }


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid journal lookup envelope")
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
    accounts = plan(policy, payload)["accounts"]
    keys = set(accounts.values())
    if set(before) != keys or set(after) != keys:
        raise BridgeError("original journal balances required")
    effects = {}
    for key in keys:
        old, new = before[key], after[key]
        if old["type"] != new["type"]:
            raise BridgeError("journal account type changed")
        delta = sum(
            (
                money(line["amount"]) * (1 if line["side"] == "debit" else -1)
                for line in payload["lines"]
                if accounts[line["account_id"]] == key
            ),
            Decimal(0),
        )
        if old["type"] not in DEBIT_NORMAL:
            delta = -delta
        if decimal_evidence(old["balance"]) + delta != decimal_evidence(new["balance"]):
            raise BridgeError("journal account balance effect differs; never resend")
        effects[key] = {"before": old["balance"], "change": str(delta), "after": new["balance"]}
    return effects
