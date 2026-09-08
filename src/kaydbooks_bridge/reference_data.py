"""Complete read-only QuickBooks reference lists for company mapping."""

import hashlib
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, company_policy_context, strict_keys
from .validation import digest

OPERATION = "reference-data.read"

# These queries cover the QuickBooks lists used to resolve the current data-entry
# batch. Item Listing is represented by every native item subtype.
QUERIES = (
    ("names", "OtherName", ("ListID", "Name", "IsActive")),
    ("names", "Employee", ("ListID", "Name", "IsActive")),
    ("tax_codes", "SalesTaxCode", ("ListID", "Name", "IsActive", "IsTaxable")),
    ("accounts", "Account", ("ListID", "Name", "FullName", "IsActive", "AccountType", "AccountNumber", "ParentRef", "Sublevel")),
    ("customers", "Customer", ("ListID", "Name", "FullName", "IsActive", "CompanyName", "FirstName", "LastName", "Phone", "Contact", "AltContact", "TermsRef", "SalesRepRef", "ParentRef", "JobStatus")),
    ("vendors", "Vendor", ("ListID", "Name", "IsActive", "CompanyName", "FirstName", "LastName", "Phone", "Contact", "AltContact", "TermsRef")),
    ("sales_reps", "SalesRep", ("ListID", "Initial", "IsActive", "SalesRepEntityRef")),
    ("payment_methods", "PaymentMethod", ("ListID", "Name", "IsActive", "PaymentMethodType")),
    ("terms", "StandardTerms", ("ListID", "Name", "IsActive", "StdDueDays", "StdDiscountDays", "DiscountPct")),
    ("terms", "DateDrivenTerms", ("ListID", "Name", "IsActive", "DayOfMonthDue", "DueNextMonthDays", "DiscountDayOfMonth", "DiscountPct")),
    ("inventory_sites", "InventorySite", ("ListID", "Name", "IsActive", "ParentSiteRef", "IsDefaultSite")),
    ("items", "ItemInventory", ("ListID", "Name", "FullName", "IsActive", "ParentRef", "SalesDesc", "SalesPrice", "PurchaseDesc", "PurchaseCost", "IncomeAccountRef", "COGSAccountRef", "AssetAccountRef", "UnitOfMeasureSetRef")),
    ("items", "ItemInventoryAssembly", ("ListID", "Name", "FullName", "IsActive", "ParentRef", "SalesDesc", "SalesPrice", "PurchaseDesc", "PurchaseCost", "IncomeAccountRef", "COGSAccountRef", "AssetAccountRef", "UnitOfMeasureSetRef")),
    ("items", "ItemNonInventory", ("ListID", "Name", "FullName", "IsActive", "ParentRef", "SalesOrPurchase", "SalesAndPurchase", "UnitOfMeasureSetRef")),
    ("items", "ItemService", ("ListID", "Name", "FullName", "IsActive", "ParentRef", "SalesOrPurchase", "SalesAndPurchase", "UnitOfMeasureSetRef")),
    ("items", "ItemOtherCharge", ("ListID", "Name", "FullName", "IsActive", "ParentRef", "SalesOrPurchase", "SalesAndPurchase", "UnitOfMeasureSetRef")),
    ("items", "ItemGroup", ("ListID", "Name", "IsActive", "ItemDesc", "UnitOfMeasureSetRef")),
    ("items", "ItemDiscount", ("ListID", "Name", "IsActive", "ItemDesc", "DiscountRate", "DiscountRatePercent", "AccountRef")),
    ("items", "ItemFixedAsset", ("ListID", "Name", "IsActive", "AssetAccountRef", "PurchaseDesc", "PurchaseCost")),
    ("items", "ItemSubtotal", ("ListID", "Name", "IsActive", "ItemDesc")),
    ("items", "ItemPayment", ("ListID", "Name", "IsActive", "ItemDesc", "PaymentMethodRef", "DepositToAccountRef")),
    ("items", "ItemSalesTax", ("ListID", "Name", "IsActive", "ItemDesc", "TaxRate", "TaxVendorRef")),
    ("items", "ItemSalesTaxGroup", ("ListID", "Name", "IsActive", "ItemDesc")),
)


def plan(policy, specification):
    strict_keys(specification, set())
    return {
        "operation": OPERATION,
        "context_sha256": digest(
            {"schema": "reference-data-v2", "policy": company_policy_context(policy)}
        ),
    }


def append_queries(request, run, _check):
    root = fromstring(request)
    batch = root[0]
    for index, (_, entity, fields) in enumerate(QUERIES, start=3):
        query = ET.SubElement(batch, entity + "QueryRq", requestID=run + str(index))
        ET.SubElement(query, "ActiveStatus").text = "All"
        for field in fields:
            ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(
        root, encoding="unicode"
    )


def _value(node):
    if not len(node):
        return node.text or ""
    result = {}
    for child in node:
        value = _value(child)
        if child.tag in result:
            existing = result[child.tag]
            result[child.tag] = existing + [value] if isinstance(existing, list) else [existing, value]
        else:
            result[child.tag] = value
    return result


def validate_response(response, run, _check):
    raw = response.encode() if isinstance(response, str) else response
    if len(raw) > 16 * 1024 * 1024:
        raise BridgeError("reference-data response exceeds size limit")
    root = fromstring(response)
    expected_count = 2 + len(QUERIES)
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != expected_count
    ):
        raise BridgeError("complete reference-data response set required")
    catalogs = {
        "names": [],
        "tax_codes": [],
        "accounts": [],
        "customers": [],
        "vendors": [],
        "sales_reps": [],
        "payment_methods": [],
        "terms": [],
        "inventory_sites": [],
        "items": [],
    }
    seen = {name: set() for name in catalogs}
    for index, (catalog, entity, _) in enumerate(QUERIES, start=3):
        answer = root[0][index - 1]
        if answer.tag != entity + "QueryRs" or answer.get("requestID") != run + str(index):
            raise BridgeError("reference-data response correlation mismatch")
        code, severity = answer.get("statusCode"), answer.get("statusSeverity")
        records = [node for node in answer if node.tag == entity + "Ret"]
        empty = not records and (code, severity) in (("1", "Info"), ("500", "Warn"))
        if not empty and (code, severity) != ("0", "Info"):
            raise BridgeError("QuickBooks reference list failed")
        if len(records) > 10000:
            raise BridgeError("QuickBooks reference list exceeds supported size")
        for node in records:
            record = _value(node)
            list_id = record.get("ListID")
            if not isinstance(list_id, str) or not list_id or list_id in seen[catalog]:
                raise BridgeError("reference list contains missing or duplicate identity")
            seen[catalog].add(list_id)
            catalogs[catalog].append({"kind": entity, **record})
    for values in catalogs.values():
        values.sort(key=lambda row: (row.get("FullName") or row.get("Name") or row.get("Initial") or "").casefold())
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), {
        "catalogs": catalogs,
        "counts": {name: len(values) for name, values in catalogs.items()},
        "complete": True,
        "active_status": "All",
        "read_only": True,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }
