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
    strict_keys(value, {"currency_id", "customers", "items"}, {"commercial"})
    if "commercial" in value:
        from .invoice_commercial import validate_policy

        validate_policy(value["commercial"])
    if value["currency_id"] is not None:
        required_id(value["currency_id"])
    for name, allowed in (("customers", customers), ("items", items)):
        mapping = value[name]
        if not isinstance(mapping, dict) or not mapping or mapping.keys() - set(allowed):
            raise BridgeError("master mapping must use company allowlist aliases")
        ids = []
        for entry in mapping.values():
            if name == "items":
                strict_keys(
                    entry,
                    {"list_id", "income_account_id"},
                    {"kind", "cogs_account_id", "asset_account_id"},
                )
                kind = entry.get("kind", "Service")
                if kind not in ("Service", "Inventory"):
                    raise BridgeError("only service and inventory items are supported")
                if kind == "Inventory":
                    if "commercial" not in value:
                        raise BridgeError("inventory requires commercial compatibility policy")
                    required_id(entry.get("cogs_account_id"))
                    required_id(entry.get("asset_account_id"))
                elif "cogs_account_id" in entry or "asset_account_id" in entry:
                    raise BridgeError("service items cannot use inventory accounts")
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
    specs = []
    for alias in aliases:
        entry = masters["items"][alias]
        specs.append({"alias": alias, "offset": len(queries) + 2, **entry})
        queries.extend(
            [
                ("Item" + entry.get("kind", "Service"), entry["list_id"]),
                ("Account", entry["income_account_id"]),
            ]
        )
        if entry.get("kind") == "Inventory":
            queries.extend(
                [("Account", entry["cogs_account_id"]), ("Account", entry["asset_account_id"])]
            )
    result = {
        "queries": queries,
        "currency": company.currency,
        "currency_id": masters["currency_id"],
        "item_count": len(aliases),
        "item_specs": specs,
        "context_sha256": digest(
            {"invoice": invoice, "masters": masters, "roles": company.account_roles}
        ),
    }
    if "commercial" in masters:
        from .invoice_commercial import extend_plan

        extend_plan(result, masters["commercial"], invoice)
    elif "tax_amount" in invoice or any("quantity" in line for line in invoice["lines"]):
        raise BridgeError("quantity, price and tax require commercial compatibility policy")
    return result


def append_queries(discovery, correlation, check):
    root = fromstring(discovery)
    batch = root.find("QBXMLMsgsRq")
    for index, (entity, list_id) in enumerate(check["queries"], 3):
        query = ET.SubElement(batch, entity + "QueryRq", requestID=f"{correlation}{index}")
        if list_id is not None:
            ET.SubElement(query, "ListID").text = list_id
        for field in check.get("fields", QUERY_FIELDS)[entity]:
            ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


PREVIEW_ENTITIES = ("Preferences", "Currency", "Customer", "ItemService", "Account")


def preview_request(discovery, correlation, *, commercial=False):
    from .invoice_commercial import FIELDS

    fields = FIELDS if commercial else QUERY_FIELDS
    entities = tuple(fields) if commercial else PREVIEW_ENTITIES
    root = fromstring(discovery)
    batch = root.find("QBXMLMsgsRq")
    for index, entity in enumerate(entities, 3):
        node = ET.SubElement(batch, entity + "QueryRq", requestID=f"{correlation}{index}")
        if entity != "Preferences":
            ET.SubElement(node, "MaxReturned").text = "20"
            ET.SubElement(node, "ActiveStatus").text = "ActiveOnly"
        for field in fields[entity]:
            ET.SubElement(node, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def preview_response(payload, correlation, *, commercial=False):
    from .invoice_commercial import FIELDS

    fields = FIELDS if commercial else QUERY_FIELDS
    entities = tuple(fields) if commercial else PREVIEW_ENTITIES
    responses = list(parse_response(payload))
    if len(responses) != len(entities) + 2:
        raise BridgeError("master preview response set mismatch")
    result = {}
    for index, (response, entity) in enumerate(zip(responses[2:], entities, strict=True), 3):
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
        empty_commercial = (
            commercial
            and entity != "Preferences"
            and response.status_code == 1
            and response.status_severity == "Info"
            and not response.records
        )
        if (
            response.entity != entity
            or response.request_id != f"{correlation}{index}"
            or (response.status_code != 0 and not unavailable_currency and not empty_commercial)
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
            rows.append({key: record[key] for key in fields[entity] if key in record})
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
    for spec in check["item_specs"]:
        offset = spec["offset"]
        item, income = records[offset : offset + 2]
        if spec.get("kind") == "Inventory":
            if (
                ref(item, "IncomeAccountRef") != income["ListID"]
                or income.get("AccountType") != "Income"
            ):
                raise BridgeError("inventory income account mismatch")
            continue
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
    if "commercial" in check:
        from .invoice_commercial import validate_commercial

        validate_commercial(records, ar_index, check)
    root = fromstring(payload)
    batch = root.find("QBXMLMsgsRs")
    for node in list(batch)[2:]:
        batch.remove(node)
    return ET.tostring(root, encoding="unicode")
