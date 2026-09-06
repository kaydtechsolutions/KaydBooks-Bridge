"""Fixed supplier/account preview for bill onboarding; not posting evidence."""

from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .account_lookup import validate_list_id
from .config import BridgeError
from .validation import digest

FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Vendor": ("ListID", "Name", "IsActive", "CurrencyRef"),
    "Account": ("ListID", "FullName", "IsActive", "AccountType", "CurrencyRef"),
}
TERMS_FIELDS = ("ListID", "Name", "IsActive", "StdDueDays", "StdDiscountDays", "DiscountPct")
SERVICE_FIELDS = (
    "ListID",
    "Name",
    "IsActive",
    "SalesOrPurchase",
    "SalesAndPurchase",
    "UnitOfMeasureSetRef",
    "IsTaxIncluded",
)


def append_preview(discovery, run):
    root = fromstring(discovery)
    for index, (entity, fields) in enumerate(FIELDS.items(), 3):
        node = ET.SubElement(root[0], entity + "QueryRq", requestID=f"{run}{index}")
        if entity != "Preferences":
            ET.SubElement(node, "MaxReturned").text = "20"
            ET.SubElement(node, "ActiveStatus").text = "ActiveOnly"
        for field in fields:
            ET.SubElement(node, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_preview(payload, run):
    responses = list(parse_response(payload))
    if len(responses) != 5:
        raise BridgeError("bill master preview response count mismatch")
    result = {}
    for index, (entity, fields) in enumerate(FIELDS.items(), 3):
        response = responses[index - 1]
        if (
            response.entity != entity
            or response.request_id != f"{run}{index}"
            or response.status_code != 0
            or response.status_severity != "Info"
            or len(response.records) > (1 if entity == "Preferences" else 20)
        ):
            raise BridgeError("bill preview status, correlation or limit mismatch")
        if entity == "Preferences" and len(response.records) != 1:
            raise BridgeError("bill preview preferences missing")
        seen = set()
        for record in response.records:
            if entity == "Preferences":
                prefs = record.get("MultiCurrencyPreferences")
                if not isinstance(prefs, dict) or prefs.get("IsMultiCurrencyOn") not in (
                    "true",
                    "false",
                ):
                    raise BridgeError("bill preview currency preferences missing")
            else:
                list_id = record.get("ListID")
                if list_id is None or record.get("IsActive") != "true" or list_id in seen:
                    raise BridgeError("bill preview duplicate, inactive or missing master")
                validate_list_id(list_id)
                seen.add(list_id)
                label = "Name" if entity == "Vendor" else "FullName"
                if not isinstance(record.get(label), str) or not record[label].strip():
                    raise BridgeError("bill preview master name missing")
                if entity == "Account" and not isinstance(record.get("AccountType"), str):
                    raise BridgeError("bill preview account type missing")
        result[entity] = [
            {field: record[field] for field in fields if field in record}
            for record in response.records
        ]
    root = fromstring(payload)
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), result


def plan(policy, payload):
    from .bills import context, validate_payload

    bill = validate_payload(payload, policy)
    binding = context(policy, bill)
    queries = [
        ("Preferences", None),
        ("Vendor", binding["vendor_list_id"]),
        ("Account", binding["payable_list_id"]),
    ]
    queries.extend(("Account", key) for key in sorted(set(binding["expense_list_ids"])))
    queries.extend(
        ("ItemService", key) for key in sorted(set(binding.get("item_list_ids", [])) - {None})
    )
    if "terms_list_id" in binding:
        queries.append(("StandardTerms", binding["terms_list_id"]))
    return {
        "queries": queries,
        "binding": binding,
        "bill": bill,
        "context_sha256": digest(
            {
                "operation": "bill-master-check",
                "payload": bill,
                "binding": binding,
                "max_total": policy.max_total,
            }
        ),
    }


def append_check(discovery, run, check):
    root = fromstring(discovery)
    for index, (entity, list_id) in enumerate(check["queries"], 3):
        node = ET.SubElement(root[0], entity + "QueryRq", requestID=f"{run}{index}")
        if list_id is not None:
            ET.SubElement(node, "ListID").text = list_id
        for field in (
            TERMS_FIELDS
            if entity == "StandardTerms"
            else SERVICE_FIELDS
            if entity == "ItemService"
            else FIELDS[entity]
        ):
            ET.SubElement(node, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_check(payload, run, check):
    responses = list(parse_response(payload))
    expected = [("Host", None), ("Company", None)] + check["queries"]
    if len(responses) != len(expected):
        raise BridgeError("bill master response set mismatch")
    records = []
    for index, (response, (entity, list_id)) in enumerate(zip(responses, expected, strict=True), 1):
        if (
            response.entity != entity
            or response.request_id != f"{run}{index}"
            or response.status_code != 0
            or response.status_severity != "Info"
            or len(response.records) != 1
        ):
            raise BridgeError("bill master status, correlation or cardinality mismatch")
        record = response.records[0]
        if list_id is not None and (
            record.get("ListID") != list_id or record.get("IsActive") != "true"
        ):
            raise BridgeError("bill master is inactive, missing or mismatched")
        records.append(record)
    preferences = records[2].get("MultiCurrencyPreferences")
    if (
        not isinstance(preferences, dict)
        or preferences.get("IsMultiCurrencyOn") != "false"
        or "HomeCurrencyRef" in preferences
        or any("CurrencyRef" in row for row in records[3:])
    ):
        raise BridgeError("initial expense bills require a verified single-currency company")
    if records[4].get("AccountType") != "AccountsPayable":
        raise BridgeError("bill payable account type mismatch")
    expense_end = 5 + len(set(check["binding"]["expense_list_ids"]))
    if any(
        row.get("AccountType") not in ("Expense", "OtherExpense") for row in records[5:expense_end]
    ):
        raise BridgeError("bill expense account type mismatch")
    items = dict(
        zip(
            sorted(set(check["binding"].get("item_list_ids", [])) - {None}),
            records[
                expense_end : expense_end
                + len(set(check["binding"].get("item_list_ids", [])) - {None})
            ],
            strict=True,
        )
    )
    for item_id, expense_id in zip(
        check["binding"].get("item_list_ids", [None] * len(check["binding"]["expense_list_ids"])),
        check["binding"]["expense_list_ids"],
        strict=True,
    ):
        if item_id is None:
            continue
        item = items[item_id]
        if "UnitOfMeasureSetRef" in item or item.get("IsTaxIncluded", "false") != "false":
            raise BridgeError("unsupported purchase item unit or tax setting")
        two_sided, single = item.get("SalesAndPurchase"), item.get("SalesOrPurchase")
        if isinstance(two_sided, dict) and single is None:
            purchase_account = two_sided.get("ExpenseAccountRef")
        elif isinstance(single, dict) and two_sided is None:
            purchase_account = single.get("AccountRef")
        else:
            raise BridgeError("ambiguous service purchase account")
        if not isinstance(purchase_account, dict) or purchase_account.get("ListID") != expense_id:
            raise BridgeError("service purchase expense account differs")
    if "terms_list_id" in check["binding"]:
        import re
        from datetime import date, timedelta
        from decimal import Decimal

        from .invoice_commercial import decimal_evidence

        term = records[-1]
        days = term.get("StdDueDays")
        if not isinstance(days, str) or not re.fullmatch(r"[0-9]{1,4}", days) or int(days) > 3650:
            raise BridgeError("unsupported standard bill terms")
        if decimal_evidence(term.get("DiscountPct")) != Decimal(0):
            raise BridgeError("discounted bill terms require a separate adapter")
        if date.fromisoformat(check["bill"]["txn_date"]) + timedelta(
            days=int(days)
        ) != date.fromisoformat(check["bill"]["due_date"]):
            raise BridgeError("bill due date differs from verified standard terms")
    root = fromstring(payload)
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode")


def append_terms_preview(discovery, run, *, services=False):
    root = fromstring(discovery)
    node = ET.SubElement(
        root[0], "ItemServiceQueryRq" if services else "StandardTermsQueryRq", requestID=run + "3"
    )
    ET.SubElement(node, "MaxReturned").text = "20"
    ET.SubElement(node, "ActiveStatus").text = "ActiveOnly"
    for field in SERVICE_FIELDS if services else TERMS_FIELDS:
        ET.SubElement(node, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_terms_preview(payload, run, *, services=False):
    rows = list(parse_response(payload))
    if (
        len(rows) != 3
        or rows[2].entity != ("ItemService" if services else "StandardTerms")
        or rows[2].request_id != run + "3"
        or rows[2].status_code != 0
        or rows[2].status_severity != "Info"
        or len(rows[2].records) > 20
    ):
        raise BridgeError("bill terms preview status, count or correlation mismatch")
    seen = set()
    for row in rows[2].records:
        if row.get("IsActive") != "true" or row.get("ListID") is None or row["ListID"] in seen:
            raise BridgeError("inactive, duplicate or missing terms")
        validate_list_id(row["ListID"])
        seen.add(row["ListID"])
    root = fromstring(payload)
    root[0].remove(root[0][2])
    return ET.tostring(root, encoding="unicode"), [
        {
            field: row[field]
            for field in (SERVICE_FIELDS if services else TERMS_FIELDS)
            if field in row
        }
        for row in rows[2].records
    ]
