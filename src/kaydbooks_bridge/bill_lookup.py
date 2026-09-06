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
    return {
        "queries": queries,
        "binding": binding,
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
        for field in FIELDS[entity]:
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
    if any(row.get("AccountType") not in ("Expense", "OtherExpense") for row in records[5:]):
        raise BridgeError("bill expense account type mismatch")
    root = fromstring(payload)
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode")
