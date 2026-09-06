"""Bounded exact invoice master checks; never constructs a transaction request."""

from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .account_lookup import validate_list_id
from .config import BridgeError, strict_keys
from .validation import digest, validate_invoice

ACCOUNT_FIELDS = ("ListID", "IsActive", "AccountType", "CurrencyRef")
QUERY_FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Currency": ("ListID", "IsActive", "CurrencyCode"),
    "Account": ACCOUNT_FIELDS,
    "Customer": ("ListID", "IsActive", "CurrencyRef"),
    "ItemService": ("ListID", "IsActive", "SalesOrPurchase", "SalesAndPurchase"),
}


def required_id(value):
    if value is None:
        raise BridgeError("explicit master ListID required")
    return validate_list_id(value)


def validate_masters(value, customers, items):
    if value == {}:
        return {}
    strict_keys(value, {"currency_id", "customers", "items"})
    if value["currency_id"] is not None:
        required_id(value["currency_id"])
    for name, allowed in (("customers", customers), ("items", items)):
        mapping = value[name]
        if not isinstance(mapping, dict) or not mapping or mapping.keys() - set(allowed):
            raise BridgeError("master mapping must use company allowlist aliases")
        ids = []
        for entry in mapping.values():
            if name == "items":
                strict_keys(entry, {"list_id", "income_account_id"})
                required_id(entry["income_account_id"])
                entry = entry["list_id"]
            ids.append(required_id(entry))
        if len(ids) != len(set(ids)):
            raise BridgeError("master aliases must have distinct ListIDs")
    # Copy nested operator policy; no mutation of loaded config during planning.
    import json

    return json.loads(json.dumps(value))


def plan(company, payload):
    invoice = validate_invoice(payload, company)
    masters = validate_masters(company.invoice_masters, company.customers, company.items)
    ar = company.account_roles.get("invoice_receivable")
    if not masters or ar is None:
        raise BridgeError("invoice masters and receivables role must be configured")
    required_id(ar)
    aliases = sorted({line["item_id"] for line in invoice["lines"]})
    if len(aliases) > 20:
        raise BridgeError("compatibility check supports at most 20 distinct service items")
    if invoice["customer_id"] not in masters["customers"] or any(
        alias not in masters["items"] for alias in aliases
    ):
        raise BridgeError("invoice master alias has no exact mapping")
    queries = [("Preferences", None)]
    if masters["currency_id"] is not None:
        queries.append(("Currency", masters["currency_id"]))
    queries += [
        ("Account", ar),
        ("Customer", masters["customers"][invoice["customer_id"]]),
    ]
    for alias in aliases:
        entry = masters["items"][alias]
        queries.extend([("ItemService", entry["list_id"]), ("Account", entry["income_account_id"])])
    return {
        "queries": queries,
        "currency": company.currency,
        "currency_id": masters["currency_id"],
        "item_count": len(aliases),
        "context_sha256": digest(
            {"invoice": invoice, "masters": masters, "roles": company.account_roles}
        ),
    }


def append_queries(discovery, correlation, check):
    root = fromstring(discovery)
    batch = root.find("QBXMLMsgsRq")
    for index, (entity, list_id) in enumerate(check["queries"], 3):
        query = ET.SubElement(batch, entity + "QueryRq", requestID=f"{correlation}{index}")
        if list_id is not None:
            ET.SubElement(query, "ListID").text = list_id
        for field in QUERY_FIELDS[entity]:
            ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


PREVIEW_ENTITIES = ("Preferences", "Currency", "Customer", "ItemService", "Account")


def preview_request(discovery, correlation):
    root = fromstring(discovery)
    batch = root.find("QBXMLMsgsRq")
    for index, entity in enumerate(PREVIEW_ENTITIES, 3):
        node = ET.SubElement(batch, entity + "QueryRq", requestID=f"{correlation}{index}")
        if entity != "Preferences":
            ET.SubElement(node, "MaxReturned").text = "20"
            ET.SubElement(node, "ActiveStatus").text = "ActiveOnly"
        for field in QUERY_FIELDS[entity]:
            ET.SubElement(node, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def preview_response(payload, correlation):
    responses = list(parse_response(payload))
    if len(responses) != 7:
        raise BridgeError("master preview response set mismatch")
    result = {}
    for index, (response, entity) in enumerate(
        zip(responses[2:], PREVIEW_ENTITIES, strict=True), 3
    ):
        unavailable_currency = (
            entity == "Currency"
            and response.status_code == 3250
            and response.status_severity == "Error"
            and not response.records
            and result.get("Preferences", [{}])[0]
            .get("MultiCurrencyPreferences", {})
            .get("IsMultiCurrencyOn")
            == "false"
        )
        if (
            response.entity != entity
            or response.request_id != f"{correlation}{index}"
            or (response.status_code != 0 and not unavailable_currency)
            or (response.status_severity != "Info" and not unavailable_currency)
            or len(response.records) > 20
        ):
            raise BridgeError("master preview status or correlation mismatch")
        seen = set()
        if entity == "Preferences" and len(response.records) != 1:
            raise BridgeError("missing or ambiguous preferences")
        rows = []
        for record in response.records:
            if entity != "Preferences":
                key = required_id(record.get("ListID"))
                if key in seen or record.get("IsActive") != "true":
                    raise BridgeError("duplicate or inactive preview master")
                seen.add(key)
            rows.append({key: record[key] for key in QUERY_FIELDS[entity] if key in record})
        result[entity] = rows
    root = fromstring(payload)
    batch = root.find("QBXMLMsgsRs")
    for node in list(batch)[2:]:
        batch.remove(node)
    return ET.tostring(root, encoding="unicode"), result


def ref(record, key):
    value = record.get(key)
    if not isinstance(value, dict):
        raise BridgeError("required master reference is missing")
    return required_id(value.get("ListID"))


def validate_response(payload, correlation, check):
    responses = list(parse_response(payload))
    expected = [("Host", None), ("Company", None)] + check["queries"]
    if len(responses) != len(expected):
        raise BridgeError("invoice master response set mismatch")
    records = []
    for index, (response, (entity, list_id)) in enumerate(zip(responses, expected, strict=True), 1):
        if (
            response.entity != entity
            or response.request_id != f"{correlation}{index}"
            or response.status_code != 0
            or response.status_severity != "Info"
            or len(response.records) != 1
        ):
            raise BridgeError("invoice master response status, correlation or cardinality mismatch")
        record = response.records[0]
        if list_id is not None and (
            record.get("ListID") != list_id or record.get("IsActive") != "true"
        ):
            raise BridgeError("invoice master is missing, inactive or mismatched")
        records.append(record)
    preferences = records[2].get("MultiCurrencyPreferences")
    if not isinstance(preferences, dict) or preferences.get("IsMultiCurrencyOn") not in (
        "true",
        "false",
    ):
        raise BridgeError("currency preferences evidence is incomplete")
    single = check["currency_id"] is None
    ar_index = 3 if single else 4
    if single:
        if preferences["IsMultiCurrencyOn"] != "false":
            raise BridgeError("single-currency policy requires multicurrency to be disabled")
        if "HomeCurrencyRef" in preferences or any(
            "CurrencyRef" in r for r in records[ar_index : ar_index + 2]
        ):
            raise BridgeError("unexpected currency references require explicit currency binding")
    else:
        if ref(preferences, "HomeCurrencyRef") != check["currency_id"]:
            raise BridgeError("configured currency is not the QuickBooks home currency")
        if records[3].get("CurrencyCode") != check["currency"]:
            raise BridgeError("invoice currency code differs from verified home currency")
        if any(
            ref(record, "CurrencyRef") != check["currency_id"]
            for record in records[ar_index : ar_index + 2]
        ):
            raise BridgeError("customer or receivables currency differs from invoice currency")
    if records[ar_index].get("AccountType") != "AccountsReceivable":
        raise BridgeError("invoice receivables account type mismatch")
    for offset in range(ar_index + 2, len(records), 2):
        item, income = records[offset : offset + 2]
        sale = item.get("SalesOrPurchase")
        both = item.get("SalesAndPurchase")
        if (sale is None) == (both is None):
            raise BridgeError("service item sales account is ambiguous or missing")
        if sale is not None:
            if not isinstance(sale, dict):
                raise BridgeError("invalid service item sales data")
            account_id = ref(sale, "AccountRef")
        else:
            if not isinstance(both, dict):
                raise BridgeError("invalid service item sales data")
            account_id = ref(both, "IncomeAccountRef")
        if account_id != income["ListID"] or income.get("AccountType") != "Income":
            raise BridgeError("service item income account mapping or type mismatch")
    root = fromstring(payload)
    batch = root.find("QBXMLMsgsRs")
    for node in list(batch)[2:]:
        batch.remove(node)
    return ET.tostring(root, encoding="unicode")
