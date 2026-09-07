"""Explicit non-tax master changes; no balances, deletions or historical account edits."""

import json
import re
from decimal import Decimal
from uuid import UUID
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, strict_keys
from .validation import canonical, digest

KINDS = {
    "customer": "Customer",
    "supplier": "Vendor",
    "service": "ItemService",
    "inventory": "ItemInventory",
    "discount": "ItemDiscount",
    "other-charge": "ItemOtherCharge",
}
SALES_KINDS = {"service", "other-charge"}
DISCOUNT = {"discount_description": ("ItemDesc", 4095), "discount_amount": ("DiscountRate", None)}
CONTACT = {"company_name": ("CompanyName", 41), "phone": ("Phone", 21), "email": ("Email", 1023)}
ITEM = {
    "sales_description": ("SalesDesc", 4095),
    "sales_price": ("SalesPrice", None),
    "purchase_description": ("PurchaseDesc", 4095),
    "purchase_cost": ("PurchaseCost", None),
}
ACCOUNTS = {
    "income_account": ("IncomeAccountRef", {"Income"}),
    "expense_account": ("ExpenseAccountRef", {"Expense", "CostOfGoodsSold"}),
    "cogs_account": ("COGSAccountRef", {"CostOfGoodsSold"}),
    "asset_account": ("AssetAccountRef", {"OtherCurrentAsset"}),
    "discount_account": ("AccountRef", {"Income"}),
}


COMMON = ("ListID", "EditSequence", "Name", "IsActive", "ExternalGUID")
FIELDS = {
    "customer": COMMON
    + (
        "FullName",
        "ParentRef",
        "CompanyName",
        "Phone",
        "Email",
        "Balance",
        "TotalBalance",
        "CurrencyRef",
    ),
    "supplier": COMMON + ("CompanyName", "Phone", "Email", "Balance", "CurrencyRef", "IsTaxAgency"),
    "service": COMMON
    + (
        "FullName",
        "ParentRef",
        "SalesOrPurchase",
        "SalesAndPurchase",
        "UnitOfMeasureSetRef",
        "IsTaxIncluded",
    ),
    "inventory": COMMON
    + (
        "FullName",
        "ParentRef",
        "SalesDesc",
        "SalesPrice",
        "IncomeAccountRef",
        "PurchaseDesc",
        "PurchaseCost",
        "COGSAccountRef",
        "AssetAccountRef",
        "QuantityOnHand",
        "AverageCost",
        "QuantityOnOrder",
        "QuantityOnSalesOrder",
        "UnitOfMeasureSetRef",
        "IsTaxIncluded",
    ),
}


FIELDS["other-charge"] = FIELDS["service"] + ("SpecialItemType",)
FIELDS["discount"] = COMMON + (
    "FullName",
    "ParentRef",
    "ItemDesc",
    "DiscountRate",
    "DiscountRatePercent",
    "AccountRef",
)


def validate(payload, policy):
    strict_keys(payload, {"ref_number", "kind", "action", "fields"}, {"target", "service_mode"})
    if (
        not isinstance(payload["kind"], str)
        or payload["kind"] not in KINDS
        or payload["action"] not in ("create", "update")
    ):
        raise BridgeError("unsupported master kind or action")
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,31}", payload["ref_number"]
    ):
        raise BridgeError("explicit bounded master proposal reference required")
    kind, action, fields = payload["kind"], payload["action"], payload["fields"]
    if kind in SALES_KINDS:
        if payload.get("service_mode") not in ("sales", "sales-purchase"):
            raise BridgeError("explicit service sales/purchase mode required")
    elif "service_mode" in payload:
        raise BridgeError("sales/purchase mode is only valid for service and other-charge items")
    is_item = kind not in ("customer", "supplier")
    attributes = DISCOUNT if kind == "discount" else ITEM if is_item else CONTACT
    allowed = {"name", "active"} | set(attributes)
    required = {"name"} if action == "create" else set()
    if kind == "discount" and action == "create":
        required |= {"discount_amount", "discount_account"}
        allowed |= {"discount_account"}
    elif is_item and action == "create":
        required |= {"sales_price", "income_account"}
        allowed |= {"income_account"}
        if kind == "inventory":
            required |= {"purchase_cost", "cogs_account", "asset_account"}
            allowed |= {"cogs_account", "asset_account"}
        elif payload.get("service_mode") == "sales-purchase":
            required |= {"purchase_cost", "expense_account"}
            allowed |= {"expense_account"}
    if kind in SALES_KINDS and payload["service_mode"] == "sales":
        allowed -= {"purchase_cost", "purchase_description", "expense_account"}
    strict_keys(fields, required, allowed - required)
    if not fields:
        raise BridgeError("explicit changed master fields required")
    if action == "update":
        target = payload.get("target")
        strict_keys(target, {"list_id", "edit_sequence", "record_sha256"})
        for name in ("list_id", "edit_sequence"):
            if not isinstance(target[name], str) or not re.fullmatch(
                r"[A-Za-z0-9-]{1,31}", target[name]
            ):
                raise BridgeError("exact ListID and EditSequence required")
        if not isinstance(target["record_sha256"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", target["record_sha256"]
        ):
            raise BridgeError("reviewed original master hash required")
    elif "target" in payload:
        raise BridgeError("creation cannot target an existing record")
    for name, value in fields.items():
        if name == "active":
            if type(value) is not bool:
                raise BridgeError("active must be explicit boolean")
        elif name in ACCOUNTS:
            if not isinstance(value, str) or value not in policy.account_roles:
                raise BridgeError("master account must be a configured company role")
        elif name in ("sales_price", "purchase_cost", "discount_amount"):
            if not isinstance(value, str) or not re.fullmatch(
                r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}", value
            ):
                raise BridgeError("master price/cost requires nonnegative two-place decimal")
        else:
            limit = (31 if is_item else 41) if name == "name" else attributes[name][1]
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
                raise BridgeError("invalid bounded master text")
            if name == "name" and (not value or value != value.strip() or ":" in value):
                raise BridgeError("explicit flat master name required")
    return json.loads(canonical(payload))


def request(payload, policy, run, *, external_guid=None):
    value = validate(payload, policy)
    kind = KINDS[value["kind"]]
    action = "Add" if value["action"] == "create" else "Mod"
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    node = ET.SubElement(ET.SubElement(batch, kind + action + "Rq", requestID=run), kind + action)
    if action == "Mod":
        ET.SubElement(node, "ListID").text = value["target"]["list_id"]
        ET.SubElement(node, "EditSequence").text = value["target"]["edit_sequence"]
    fields = value["fields"]
    if action == "Add":
        try:
            external_guid = "{" + str(UUID(external_guid)).upper() + "}"
        except (ValueError, TypeError, AttributeError) as exc:
            raise BridgeError("durable master creation identity required") from exc
    if "name" in fields:
        ET.SubElement(node, "Name").text = fields["name"]
    if "active" in fields or action == "Add":
        ET.SubElement(node, "IsActive").text = str(fields.get("active", True)).lower()
    if value["kind"] in ("customer", "supplier"):
        for name, (tag, _) in CONTACT.items():
            if name in fields:
                ET.SubElement(node, tag).text = fields[name]
    elif value["kind"] == "discount":
        for name, (tag, _) in DISCOUNT.items():
            if name in fields:
                ET.SubElement(node, tag).text = fields[name]
        if "discount_account" in fields:
            ET.SubElement(ET.SubElement(node, "AccountRef"), "ListID").text = policy.account_roles[
                fields["discount_account"]
            ]
    else:
        if not any(name in fields for name in set(ITEM) | set(ACCOUNTS)):
            return xml(root)
        target = node
        if value["kind"] in SALES_KINDS:
            purchased = value["service_mode"] == "sales-purchase"
            # Updates must retain the existing sales/purchase aggregate shape.
            target = ET.SubElement(
                node,
                ("SalesAndPurchase" if purchased else "SalesOrPurchase")
                + ("Mod" if action == "Mod" else ""),
            )
            if not purchased:
                if "sales_description" in fields:
                    ET.SubElement(target, "Desc").text = fields["sales_description"]
                if "sales_price" in fields:
                    ET.SubElement(target, "Price").text = fields["sales_price"]
                if "income_account" in fields:
                    ET.SubElement(
                        ET.SubElement(target, "AccountRef"), "ListID"
                    ).text = policy.account_roles[fields["income_account"]]
                if action == "Add":
                    ET.SubElement(node, "ExternalGUID").text = external_guid
                return xml(root)
        # Native order is significant. Accounts are added only on creation.
        for name in (
            "sales_description",
            "sales_price",
            "income_account",
            "purchase_description",
            "purchase_cost",
            "expense_account",
            "cogs_account",
            "asset_account",
        ):
            if name not in fields:
                continue
            if name in ACCOUNTS:
                ET.SubElement(
                    ET.SubElement(target, ACCOUNTS[name][0]), "ListID"
                ).text = policy.account_roles[fields[name]]
            else:
                ET.SubElement(target, ITEM[name][0]).text = fields[name]
    if action == "Add":
        ET.SubElement(node, "ExternalGUID").text = external_guid
    return xml(root)


def xml(root):
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def record(node, fields=None):
    """Stable native projection; retain every leaf and refuse duplicate elements."""

    def convert(n):
        if not len(n):
            return n.text or ""
        result = {}
        for child in n:
            if child.tag in result:
                raise BridgeError("unsupported repeated master field")
            result[child.tag] = convert(child)
        return result

    if fields is None:
        return convert(node)
    selected = ET.Element(node.tag)
    selected.extend(child for child in node if child.tag in fields)
    return convert(selected)


def protected(value):
    # Retain original values not explicitly changed. Sequence/time metadata may advance.
    return {
        k: v for k, v in value.items() if k not in {"TimeCreated", "TimeModified", "EditSequence"}
    }


def target_reference(value):
    if not all(isinstance(value.get(k), str) and value[k] for k in ("ListID", "EditSequence")):
        raise BridgeError("native master identity and edit sequence required")
    return {
        "list_id": value["ListID"],
        "edit_sequence": value["EditSequence"],
        "record_sha256": digest(value),
    }


def compare(payload, policy, saved, original=None):
    value = validate(payload, policy)
    target_reference(saved)
    fields = value["fields"]
    if original is not None:
        if saved["ListID"] != original["ListID"]:
            raise BridgeError("saved master identity changed")
        expected = protected(original)
    else:
        expected = {}
    for name in ("name", "active"):
        if name in fields or (name == "active" and original is None):
            expected["Name" if name == "name" else "IsActive"] = (
                fields[name] if name == "name" else str(fields.get(name, True)).lower()
            )
    if "name" in fields and value["kind"] != "supplier":
        expected["FullName"] = fields["name"]
    if value["kind"] in ("customer", "supplier"):
        for name, (tag, _) in CONTACT.items():
            if name in fields:
                expected[tag] = fields[name]
    elif value["kind"] == "discount":
        for name, (tag, _) in DISCOUNT.items():
            if name in fields:
                expected[tag] = fields[name]
        if "discount_account" in fields:
            expected["AccountRef"] = {"ListID": policy.account_roles[fields["discount_account"]]}
    else:
        container = expected
        if value["kind"] in SALES_KINDS:
            purchased = value["service_mode"] == "sales-purchase"
            aggregate = "SalesAndPurchase" if purchased else "SalesOrPurchase"
            if original is not None and aggregate not in original:
                raise BridgeError("service aggregate conversion is unsupported")
            container = expected.setdefault(aggregate, {}).copy()
            expected[aggregate] = container
            if not purchased:
                for name, tag in (("sales_description", "Desc"), ("sales_price", "Price")):
                    if name in fields:
                        container[tag] = fields[name]
                if "income_account" in fields:
                    container["AccountRef"] = {
                        "ListID": policy.account_roles[fields["income_account"]]
                    }
        for name, (tag, _) in ITEM.items():
            if name in fields and (value["kind"] not in SALES_KINDS or purchased):
                container[tag] = fields[name]
        for name, (tag, _) in ACCOUNTS.items():
            if name in fields and (value["kind"] not in SALES_KINDS or purchased):
                container[tag] = {"ListID": policy.account_roles[fields[name]]}

    def matches(want, actual, field=None):
        if isinstance(want, dict):
            return isinstance(actual, dict) and all(
                matches(v, actual.get(k, ""), k) for k, v in want.items()
            )
        if field in {
            "Price",
            "DiscountRate",
            "SalesPrice",
            "PurchaseCost",
            "Balance",
            "TotalBalance",
            "QuantityOnHand",
            "AverageCost",
            "QuantityOnOrder",
            "QuantityOnSalesOrder",
        }:
            try:
                return Decimal(want) == Decimal(actual)
            except Exception:
                return False
        return want == actual

    if not matches(expected, saved):
        raise BridgeError("saved master fields or preserved values differ")
    if original is None and any(
        Decimal(saved.get(k, "0")) != 0
        for k in ("Balance", "TotalBalance", "QuantityOnHand", "TotalValue")
    ):
        raise BridgeError("new master unexpectedly contains opening amounts")
    return {
        "list_id": saved["ListID"],
        "edit_sequence": saved["EditSequence"],
        "record_sha256": digest(saved),
        "record": saved,
    }


def response(text, kind, action, run):
    root = fromstring(text)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 1:
        raise BridgeError("invalid master response envelope")
    rs = root[0][0]
    if (
        rs.tag != KINDS[kind] + action + "Rs"
        or rs.get("requestID") != run
        or rs.get("statusCode") != "0"
        or rs.get("statusSeverity") != "Info"
        or len(rs) != 1
        or rs[0].tag != KINDS[kind] + "Ret"
    ):
        raise BridgeError("unsuccessful or uncorrelated master response")
    return record(rs[0], FIELDS[kind])
