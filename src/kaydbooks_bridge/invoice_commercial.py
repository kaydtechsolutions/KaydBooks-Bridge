"""US simple invoice checks against configured tax and list-price policy; no writes."""

import re
from decimal import ROUND_HALF_UP, Decimal

from .config import BridgeError, strict_keys
from .validation import positive_decimal

FIELDS = {
    "Preferences": (
        "MultiCurrencyPreferences",
        "SalesTaxPreferences",
        "SalesAndCustomersPreferences",
        "PurchasesAndVendorsPreferences",
        "MultiLocationInventoryPreferences",
        "ItemsAndInventoryPreferences",
    ),
    "Currency": ("ListID", "IsActive", "CurrencyCode"),
    "Account": ("ListID", "IsActive", "AccountType", "CurrencyRef"),
    "Customer": (
        "ListID",
        "IsActive",
        "CurrencyRef",
        "SalesTaxCodeRef",
        "ItemSalesTaxRef",
        "PriceLevelRef",
    ),
    "ItemService": (
        "ListID",
        "IsActive",
        "SalesOrPurchase",
        "SalesAndPurchase",
        "SalesTaxCodeRef",
        "UnitOfMeasureSetRef",
        "IsTaxIncluded",
    ),
    "ItemInventory": (
        "ListID",
        "IsActive",
        "SalesPrice",
        "IncomeAccountRef",
        "COGSAccountRef",
        "AssetAccountRef",
        "QuantityOnHand",
        "QuantityOnSalesOrder",
        "SalesTaxCodeRef",
        "UnitOfMeasureSetRef",
        "IsTaxIncluded",
    ),
    "SalesTaxCode": ("ListID", "IsActive", "IsTaxable"),
    "ItemSalesTax": ("ListID", "IsActive", "TaxRate"),
}


def decimal_evidence(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?", value
    ):
        raise BridgeError("missing or invalid decimal master evidence")
    return Decimal(value)


def validate_policy(policy):
    from .invoice_compatibility import required_id

    strict_keys(policy, {"sales_tax_code_id", "tax_item_id", "tax_rate", "pricing", "inventory"})
    required_id(policy["sales_tax_code_id"])
    rate = decimal_evidence(policy["tax_rate"])
    if not 0 <= rate <= 100:
        raise BridgeError("configured tax rate is out of range")
    if policy["tax_item_id"] is not None:
        required_id(policy["tax_item_id"])
    elif rate != 0:
        raise BridgeError("taxable invoices require an exact sales-tax item")
    if policy["pricing"] != "list-price" or policy["inventory"] != "uncommitted-on-hand":
        raise BridgeError("unsupported pricing or inventory policy")


def extend_plan(check, policy, invoice):
    validate_policy(policy)
    if "tax_amount" not in invoice:
        raise BridgeError("commercial invoice requires explicit tax amount")
    for line in invoice["lines"]:
        positive_decimal(line.get("quantity"))
        positive_decimal(line.get("unit_price"))
    subtotal = sum(Decimal(line["amount"]) for line in invoice["lines"])
    expected = (subtotal * Decimal(policy["tax_rate"]) / 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if Decimal(invoice["tax_amount"]) != expected:
        raise BridgeError("invoice tax differs from configured subtotal tax calculation")
    check.update(
        fields=FIELDS, commercial=policy, invoice=invoice, tax_offset=len(check["queries"]) + 2
    )
    check["queries"].append(("SalesTaxCode", policy["sales_tax_code_id"]))
    if policy["tax_item_id"] is not None:
        check["queries"].append(("ItemSalesTax", policy["tax_item_id"]))


def validate_commercial(records, ar_index, check):
    from .invoice_compatibility import ref

    policy, invoice = check["commercial"], check["invoice"]
    prefs, customer = records[2], records[ar_index + 1]
    tax_code = records[check["tax_offset"]]
    taxable = policy["tax_item_id"] is not None
    if tax_code.get("IsTaxable") != ("true" if taxable else "false"):
        raise BridgeError("configured taxability differs from sales-tax code")
    if ref(customer, "SalesTaxCodeRef") != policy["sales_tax_code_id"]:
        raise BridgeError("customer sales-tax code differs from invoice policy")
    if "PriceLevelRef" in customer:
        raise BridgeError("customer price levels are not qualified")
    tax_prefs = prefs.get("SalesTaxPreferences")
    if tax_prefs is None and not taxable:
        tax_prefs = {}
    if not isinstance(tax_prefs, dict) or tax_prefs.get("IsUsingAmountsIncludeTax") not in (
        None,
        "false",
    ):
        raise BridgeError("tax preferences are missing or tax-inclusive pricing is enabled")
    if taxable:
        if ref(customer, "ItemSalesTaxRef") != policy["tax_item_id"]:
            raise BridgeError("customer tax item differs from invoice policy")
        if decimal_evidence(records[check["tax_offset"] + 1].get("TaxRate")) != Decimal(
            policy["tax_rate"]
        ):
            raise BridgeError("sales-tax rate differs from configured rate")
    for spec in check["item_specs"]:
        item = records[spec["offset"]]
        if "UnitOfMeasureSetRef" in item or item.get("IsTaxIncluded") not in (None, "false"):
            raise BridgeError("units of measure or inclusive item pricing are not qualified")
        if ref(item, "SalesTaxCodeRef") != policy["sales_tax_code_id"]:
            raise BridgeError("item sales-tax code differs from invoice policy")
        lines = [line for line in invoice["lines"] if line["item_id"] == spec["alias"]]
        if spec.get("kind") == "Inventory":
            price = decimal_evidence(item.get("SalesPrice"))
            for key, expected_type, offset in (
                ("COGSAccountRef", "CostOfGoodsSold", 2),
                ("AssetAccountRef", "OtherCurrentAsset", 3),
            ):
                account = records[spec["offset"] + offset]
                if (
                    ref(item, key) != account["ListID"]
                    or account.get("AccountType") != expected_type
                ):
                    raise BridgeError("inventory account reference or type mismatch")
            inventory = prefs.get("PurchasesAndVendorsPreferences", {})
            location = prefs.get("MultiLocationInventoryPreferences", {})
            tracking = prefs.get("ItemsAndInventoryPreferences", {})
            if any(not isinstance(value, dict) for value in (inventory, location, tracking)):
                raise BridgeError("inventory preference evidence is malformed")
            if (
                inventory.get("IsUsingInventory") != "true"
                or location.get("IsMultiLocationInventoryEnabled") != "false"
                or tracking.get("IsTrackingSerialOrLotNumber") != "None"
                or tracking.get("IsRSBEnabled") != "false"
            ):
                raise BridgeError(
                    "inventory preferences need verified single-location, no serial/lot/bin tracking"
                )
            on_hand = decimal_evidence(item.get("QuantityOnHand"))
            committed = decimal_evidence(item.get("QuantityOnSalesOrder"))
            needed = sum(positive_decimal(line["quantity"]) for line in lines)
            if committed < 0 or on_hand - committed < needed:
                raise BridgeError("insufficient uncommitted inventory on hand")
        else:
            sale = item.get("SalesOrPurchase")
            if sale is not None:
                if "PricePercent" in sale:
                    raise BridgeError("percentage-priced service items are not qualified")
                price = decimal_evidence(sale.get("Price"))
            else:
                price = decimal_evidence(item["SalesAndPurchase"].get("SalesPrice"))
        if any(positive_decimal(line["unit_price"]) != price for line in lines):
            raise BridgeError("invoice unit price differs from verified item list price")
