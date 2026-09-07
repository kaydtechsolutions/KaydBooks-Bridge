"""Site-to-site stock transfers with exact per-site and total-stock readback."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from . import journal_entries as common
from .config import BridgeError, identifier, strict_keys
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import digest, money

FIELDS = ("TxnID", "EditSequence", "TxnDate", "RefNumber", "FromInventorySiteRef",
          "ToInventorySiteRef", "Memo", "TransferInventoryLineRet")


def quantity(value):
    result = decimal_evidence(value)
    if result <= 0 or result > 1000000 or result.as_tuple().exponent < -6:
        raise BridgeError("transfer quantity must be positive, at most one million, with at most six decimals")
    return result


def validate_masters(value):
    if value == {}:
        return {}
    strict_keys(value, {"sites", "items"})
    for kind, minimum in (("sites", 2), ("items", 1)):
        if not isinstance(value[kind], dict) or not minimum <= len(value[kind]) <= 1000:
            raise BridgeError("configure inventory sites and stock item aliases")
        for alias, native in value[kind].items():
            identifier(alias)
            required_id(native)
    return value


def validate_payload(payload, policy):
    strict_keys(payload, {"txn_date", "ref_number", "currency", "from_site_id", "to_site_id", "lines"}, {"memo"})
    maps = validate_masters(policy.inventory_transfer_masters)
    if not maps or payload["currency"] != policy.currency:
        raise BridgeError("configured inventory transfer masters and company currency required")
    for key in ("from_site_id", "to_site_id"):
        identifier(payload[key])
        if payload[key] not in maps["sites"]:
            raise BridgeError("configured inventory site required")
    if maps["sites"][payload["from_site_id"]] == maps["sites"][payload["to_site_id"]]:
        raise BridgeError("source and destination sites must differ")
    try:
        if date.fromisoformat(payload["txn_date"]).isoformat() != payload["txn_date"]:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise BridgeError("transfer date requires YYYY-MM-DD") from exc
    ref = payload["ref_number"]
    if not isinstance(ref, str) or not 1 <= len(ref) <= 11 or not ref.isascii() or not all(c.isalnum() or c == "-" for c in ref):
        raise BridgeError("transfer reference requires 1-11 ASCII letters, digits or hyphens")
    if not isinstance(payload["lines"], list) or not 1 <= len(payload["lines"]) <= 20:
        raise BridgeError("transfer requires 1-20 stock lines")
    normalized = []
    ids = set()
    for line in payload["lines"]:
        strict_keys(line, {"item_id", "quantity"})
        identifier(line["item_id"])
        if line["item_id"] not in maps["items"] or maps["items"][line["item_id"]] in ids:
            raise BridgeError("distinct configured transfer stock items required")
        ids.add(maps["items"][line["item_id"]])
        normalized.append({**line, "quantity": format(quantity(line["quantity"]), "f")})
    if "memo" in payload:
        common.memo(payload["memo"])
    return {**payload, "lines": normalized}


def plan(policy, payload):
    payload = validate_payload(payload, policy)
    maps = policy.inventory_transfer_masters
    result = {"payload": payload, "from": maps["sites"][payload["from_site_id"]],
              "to": maps["sites"][payload["to_site_id"]],
              "items": {line["item_id"]: maps["items"][line["item_id"]] for line in payload["lines"]},
              "max_total": policy.max_total}
    return {**result, "context_sha256": digest({"schema": "inventory-transfer-v1", **result})}


def specs(check):
    result = [("Preferences", None, ("MultiLocationInventoryPreferences", "ItemsAndInventoryPreferences", "MultiCurrencyPreferences"))]
    for site in (check["from"], check["to"]):
        result.append(("InventorySite", site, ("ListID", "IsActive", "ParentSiteRef")))
    for key in sorted(check["items"].values()):
        result.append(("ItemInventory", key, ("ListID", "IsActive", "QuantityOnHand", "AverageCost")))
        for site in (check["from"], check["to"]):
            result.append(("ItemSites", (key, site), ("ListID", "ItemInventoryRef", "InventorySiteRef", "InventorySiteLocationRef", "QuantityOnHand")))
    return result


def append_check(discovery, run, check):
    root = fromstring(discovery)
    for i, (kind, key, fields) in enumerate(specs(check), 3):
        q = E.SubElement(root[0], kind + "QueryRq", requestID=run + str(i))
        if kind == "ItemSites":
            filters = E.SubElement(q, "ItemSiteFilter")
            common.ref(filters, "ItemFilter", key[0])
            common.ref(filters, "SiteFilter", key[1])
            E.SubElement(q, "MaxReturned").text = "2"
            E.SubElement(q, "ActiveStatus").text = "ActiveOnly"
        elif key is not None:
            E.SubElement(q, "ListID").text = key
        for field in fields:
            E.SubElement(q, "IncludeRetElement").text = field
    return common.render(root)


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    expected = specs(check)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 2 + len(expected):
        raise BridgeError("inventory transfer master response set differs")
    rows = list(parse_response(xml))
    balances = {"items": {}, "sites": {}}
    for i, (rs, (kind, key, _)) in enumerate(zip(rows[2:], expected, strict=True), 3):
        if (rs.entity != kind or rs.request_id != run + str(i) or rs.status_code != 0
            or rs.status_severity != "Info" or len(rs.records) != 1):
            raise BridgeError("transfer master response missing, unsuccessful or ambiguous")
        row = rs.records[0]
        if kind == "Preferences":
            location = row.get("MultiLocationInventoryPreferences", {})
            inventory = row.get("ItemsAndInventoryPreferences", {})
            if (location.get("IsMultiLocationInventoryEnabled") != "true"
                or row.get("MultiCurrencyPreferences", {}).get("IsMultiCurrencyOn") != "false"
                or inventory.get("IsTrackingSerialOrLotNumber") != "None"
                or inventory.get("FIFOEnabled") != "false"):
                raise BridgeError("transfer needs multi-location inventory enabled, single currency, average cost and no serial/lot tracking")
        elif kind == "ItemSites":
            if (row.get("ItemInventoryRef", {}).get("ListID") != key[0]
                or row.get("InventorySiteRef", {}).get("ListID") != key[1]
                or "InventorySiteLocationRef" in row):
                raise BridgeError("transfer item/site identity differs or bins require qualification")
            required_id(row.get("ListID"))
            balances["sites"].setdefault(key[0], {})[key[1]] = str(decimal_evidence(row.get("QuantityOnHand")))
        else:
            if row.get("ListID") != key or row.get("IsActive") != "true" or "ParentSiteRef" in row:
                raise BridgeError("transfer master identity/activity differs")
            if kind == "ItemInventory":
                cost = decimal_evidence(row.get("AverageCost"))
                if cost < 0:
                    raise BridgeError("negative inventory cost requires review")
                balances["items"][key] = {"quantity": str(decimal_evidence(row.get("QuantityOnHand"))), "average_cost": str(cost)}
    value = Decimal(0)
    for line in check["payload"]["lines"]:
        key = check["items"][line["item_id"]]
        q = quantity(line["quantity"])
        if not recovering and decimal_evidence(balances["sites"][key][check["from"]]) < q:
            raise BridgeError("insufficient stock at source inventory site")
        value += q * decimal_evidence(balances["items"][key]["average_cost"])
    if value > money(check["max_total"]):
        raise BridgeError("transfer stock value exceeds company limit")
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return E.tostring(root, encoding="unicode"), balances


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = E.Element("QBXML")
    rq = E.SubElement(E.SubElement(root, "QBXMLMsgsRq", onError="stopOnError"), "TransferInventoryAddRq", requestID=run)
    row = E.SubElement(rq, "TransferInventoryAdd")
    E.SubElement(row, "TxnDate").text = payload["txn_date"]
    E.SubElement(row, "RefNumber").text = payload["ref_number"]
    common.ref(row, "FromInventorySiteRef", check["from"])
    common.ref(row, "ToInventorySiteRef", check["to"])
    if "memo" in payload:
        E.SubElement(row, "Memo").text = payload["memo"]
    for line in check["payload"]["lines"]:
        node = E.SubElement(row, "TransferInventoryLineAdd")
        common.ref(node, "ItemRef", check["items"][line["item_id"]])
        E.SubElement(node, "QuantityToTransfer").text = line["quantity"]
    return common.render(root)


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("one inventory transfer identity required")
    root = fromstring(discovery)
    q = E.SubElement(root[0], "TransferInventoryQueryRq", requestID=run)
    E.SubElement(q, "TxnID" if txn_id else "RefNumber").text = txn_id or ref_number
    E.SubElement(q, "IncludeLineItems").text = "true"
    for field in FIELDS:
        E.SubElement(q, "IncludeRetElement").text = field
    return common.render(root)


def validate_receipt(xml, policy, payload, run, *, operation="TransferInventoryQuery", txn_id=None):
    check = plan(policy, payload)
    root = fromstring(xml)
    if (operation not in ("TransferInventoryAdd", "TransferInventoryQuery") or root.tag != "QBXML"
        or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 1):
        raise BridgeError("exact saved inventory transfer required")
    rs = root[0][0]
    if (rs.tag != operation + "Rs" or rs.get("requestID") != run or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info" or len(rs) != 1 or rs[0].tag != "TransferInventoryRet"):
        raise BridgeError("transfer status/correlation differs")
    row = rs[0]
    native = required_id(common.scalar(row, "TxnID"))
    required_id(common.scalar(row, "EditSequence"))
    if txn_id is not None and txn_id != native:
        raise BridgeError("saved transfer identity differs")
    for tag, key in (("TxnDate", "txn_date"), ("RefNumber", "ref_number")):
        if common.scalar(row, tag) != payload[key]:
            raise BridgeError("saved transfer header differs")
    for tag, key in (("FromInventorySiteRef", "from"), ("ToInventorySiteRef", "to")):
        if common.reference(row, tag) != check[key]:
            raise BridgeError("saved transfer site differs")
    if len(row.findall("Memo")) > 1 or (row.findtext("Memo") or None) != payload.get("memo"):
        raise BridgeError("saved transfer memo differs")
    lines = row.findall("TransferInventoryLineRet")
    if len(lines) != len(payload["lines"]):
        raise BridgeError("saved transfer line count differs")
    ids = []
    for node, line in zip(lines, payload["lines"], strict=True):
        ids.append(required_id(common.scalar(node, "TxnLineID")))
        if (common.reference(node, "ItemRef") != check["items"][line["item_id"]]
            or quantity(common.scalar(node, "QuantityTransferred")) != quantity(line["quantity"])
            or any(node.find(f) is not None for f in ("FromInventorySiteLocationRef", "ToInventorySiteLocationRef", "SerialNumber", "LotNumber"))):
            raise BridgeError("saved transfer line differs")
    if len(set(ids)) != len(ids):
        raise BridgeError("ambiguous saved transfer lines")
    return {"txn_id": native, "ref_number": payload["ref_number"], "line_ids": ids,
            "quantity_transferred": str(sum(quantity(line["quantity"]) for line in payload["lines"])),
            "verification": "matched-saved-inventory-transfer"}


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id)


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid transfer lookup envelope")
    row = root[0][-1]
    root[0].remove(row)
    discovery, balances = validate_check(E.tostring(root), run, plan(policy, payload), recovering=True)
    isolated = E.Element("QBXML")
    E.SubElement(isolated, "QBXMLMsgsRs").append(row)
    return discovery, {**validate_receipt(E.tostring(isolated), policy, payload, run + "99", txn_id=txn_id), "balances": balances}


def verify_balance_effect(payload, before, after, *, policy):
    check = plan(policy, payload)
    keys = set(check["items"].values())
    if any(set(group) != keys for group in (before["items"], after["items"], before["sites"], after["sites"])):
        raise BridgeError("original complete transfer balances required")
    effects = {}
    for line in payload["lines"]:
        key = check["items"][line["item_id"]]
        old, new = before["items"][key], after["items"][key]
        if any(decimal_evidence(old[f]) != decimal_evidence(new[f]) for f in ("quantity", "average_cost")):
            raise BridgeError("transfer changed company stock total or average cost; never resend")
        if set(before["sites"][key]) != {check["from"], check["to"]} or set(after["sites"][key]) != {check["from"], check["to"]}:
            raise BridgeError("original source and destination balances required")
        q = quantity(line["quantity"])
        for site, delta in ((check["from"], -q), (check["to"], q)):
            if decimal_evidence(before["sites"][key][site]) + delta != decimal_evidence(after["sites"][key][site]):
                raise BridgeError("transfer site stock effect differs; never resend")
        effects[key] = {"quantity": str(q), "before": before["sites"][key], "after": after["sites"][key], "company_stock_unchanged": new}
    return effects
