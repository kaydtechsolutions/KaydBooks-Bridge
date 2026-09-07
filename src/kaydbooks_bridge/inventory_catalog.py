"""Read-only inventory-site discovery for private company configuration."""

from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError, strict_keys
from .invoice_compatibility import required_id
from .journal_entries import render
from .validation import digest

OPERATION = "inventory-sites.read"


def plan(company, payload, txn_id=None):
    strict_keys(payload, set())
    if txn_id is not None:
        raise BridgeError("inventory-site discovery has no transaction selector")
    return {
        "operation": OPERATION,
        "context_sha256": digest({"schema": "inventory-sites-v1", "company": company.id}),
    }


def append_request(request, run):
    root = fromstring(request)
    q = E.SubElement(root[0], "PreferencesQueryRq", requestID=run + "3")
    for field in ("MultiLocationInventoryPreferences", "ItemsAndInventoryPreferences"):
        E.SubElement(q, "IncludeRetElement").text = field
    q = E.SubElement(root[0], "InventorySiteQueryRq", requestID=run + "4")
    E.SubElement(q, "ActiveStatus").text = "ActiveOnly"
    for field in ("ListID", "Name", "IsActive", "ParentSiteRef", "IsDefaultSite"):
        E.SubElement(q, "IncludeRetElement").text = field
    return render(root)


def validate_response(response, run):
    root = fromstring(response)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 4:
        raise BridgeError("exact inventory-site response set required")
    rows = list(parse_response(response))
    prefs, sites = rows[2:]
    if (
        prefs.entity != "Preferences"
        or prefs.request_id != run + "3"
        or prefs.status_code != 0
        or prefs.status_severity != "Info"
        or len(prefs.records) != 1
        or sites.entity != "InventorySite"
        or sites.request_id != run + "4"
    ):
        raise BridgeError("inventory-site response is unsuccessful or uncorrelated")
    no_sites = not sites.records and (sites.status_code, sites.status_severity) in (
        (1, "Info"),
        (500, "Warn"),
    )
    if not no_sites and (sites.status_code != 0 or sites.status_severity != "Info"):
        raise BridgeError("inventory sites could not be read")
    if len(sites.records) > 1000:
        raise BridgeError("inventory-site catalog exceeds supported setup size")
    result = []
    ids = set()
    for row in sites.records:
        key = required_id(row.get("ListID"))
        if key in ids or row.get("IsActive") != "true" or not isinstance(row.get("Name"), str):
            raise BridgeError("inventory-site catalog contains ambiguous records")
        ids.add(key)
        if "ParentSiteRef" not in row:
            result.append(
                {"list_id": key, "name": row["Name"], "default": row.get("IsDefaultSite") == "true"}
            )
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return E.tostring(root, encoding="unicode"), {
        "sites": result,
        "preferences": prefs.records[0],
        "read_only": True,
    }
