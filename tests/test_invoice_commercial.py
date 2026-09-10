"""Commercial compatibility through real callback/lifecycle contracts; synthetic masters."""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.invoice_compatibility import append_queries, plan, validate_response
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.qbwc_invoices import invoice_job
from kaydbooks_bridge.service import Bridge
from test_direct_sdk import direct  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_qbwc_discovery import (  # noqa: F401
    COMPANY_A,
    HOST,
    PASSWORD_A,
    authenticate,
    call,
    discovery_setup,
    receive,
)
from test_qbwc_invoices import send


@pytest.fixture
def commercial(setup_invoice):  # noqa: F811
    path, token, payload = setup_invoice
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].append("prepare")
    masters = raw["companies"]["company-a"]["invoice_masters"]
    masters["commercial"] = {
        "sales_tax_code_id": "tax-code",
        "tax_item_id": "tax-item",
        "tax_rate": "10",
        "pricing": "list-price",
        "inventory": "uncommitted-on-hand",
    }
    payload["lines"][0].update(quantity="2", unit_price="5.00", amount="10.00")
    payload["tax_amount"] = "1.00"
    path.write_text(json.dumps(raw))
    return path, token, payload


def response(request, *, inventory=False, taxable=True, mutate=None):
    prefs = {
        "MultiCurrencyPreferences": {
            "IsMultiCurrencyOn": "true",
            "HomeCurrencyRef": {"ListID": "usd-id"},
        },
        "SalesTaxPreferences": {"IsUsingAmountsIncludeTax": "false"},
        "PurchasesAndVendorsPreferences": {"IsUsingInventory": "true"},
        "MultiLocationInventoryPreferences": {"IsMultiLocationInventoryEnabled": "false"},
        "ItemsAndInventoryPreferences": {
            "EnhancedInventoryReceivingEnabled": "false",
            "FIFOEnabled": "false",
            "IsInventoryExpirationDateEnabled": "false",
            "IsTrackingSerialOrLotNumber": "None",
            "IsRSBEnabled": "false",
        },
    }

    def ref(value):
        return {"ListID": value}

    rows = {
        ("Host", None): {**HOST, "SupportedQBXMLVersion": ["17.0"]},
        ("Company", None): COMPANY_A,
        ("Preferences", None): prefs,
        ("Currency", "usd-id"): {"ListID": "usd-id", "IsActive": "true", "CurrencyCode": "USD"},
        ("Account", "ar-id"): {
            "ListID": "ar-id",
            "IsActive": "true",
            "AccountType": "AccountsReceivable",
            "CurrencyRef": ref("usd-id"),
        },
        ("Customer", "customer-id"): {
            "ListID": "customer-id",
            "IsActive": "true",
            "CurrencyRef": ref("usd-id"),
            "SalesTaxCodeRef": ref("tax-code"),
            "ItemSalesTaxRef": ref("tax-item"),
        },
        ("Account", "income-id"): {
            "ListID": "income-id",
            "IsActive": "true",
            "AccountType": "Income",
        },
        ("Account", "cogs-id"): {
            "ListID": "cogs-id",
            "IsActive": "true",
            "AccountType": "CostOfGoodsSold",
        },
        ("Account", "asset-id"): {
            "ListID": "asset-id",
            "IsActive": "true",
            "AccountType": "OtherCurrentAsset",
        },
        ("ItemService", "service-id"): {
            "ListID": "service-id",
            "IsActive": "true",
            "SalesTaxCodeRef": ref("tax-code"),
            "SalesOrPurchase": {"AccountRef": ref("income-id"), "Price": "5.00"},
        },
        ("ItemInventory", "service-id"): {
            "ListID": "service-id",
            "IsActive": "true",
            "SalesTaxCodeRef": ref("tax-code"),
            "SalesPrice": "5.00",
            "IncomeAccountRef": ref("income-id"),
            "COGSAccountRef": ref("cogs-id"),
            "AssetAccountRef": ref("asset-id"),
            "QuantityOnHand": "5",
            "AverageCost": "5.00",
            "QuantityOnSalesOrder": "1",
        },
        ("SalesTaxCode", "tax-code"): {
            "ListID": "tax-code",
            "IsActive": "true",
            "IsTaxable": "true" if taxable else "false",
        },
        ("ItemSalesTax", "tax-item"): {"ListID": "tax-item", "IsActive": "true", "TaxRate": "10"},
    }
    if not taxable:
        # QuickBooks omits SalesTaxPreferences when the company does not charge tax.
        # A non-taxable code must still be independently verified on both masters.
        prefs.pop("SalesTaxPreferences")
        rows[("Customer", "customer-id")].pop("ItemSalesTaxRef")
    if mutate:
        mutate(rows)
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRs")

    def fields(parent, record):
        for key, value in record.items():
            for v in value if isinstance(value, list) else [value]:
                node = ET.SubElement(parent, key)
                if isinstance(v, dict):
                    fields(node, v)
                else:
                    node.text = v

    for query in ET.fromstring(request).find("QBXMLMsgsRq"):
        entity = query.tag.removesuffix("QueryRq")
        row = rows[(entity, query.findtext("ListID"))]
        rs = ET.SubElement(
            batch,
            entity + "QueryRs",
            requestID=query.attrib["requestID"],
            statusCode="0",
            statusSeverity="Info",
        )
        fields(ET.SubElement(rs, entity + "Ret"), row)
    return ET.tostring(root, encoding="unicode")


def inventory_policy(setup):
    path, _, payload = setup
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["items"][
        payload["lines"][0]["item_id"]
    ].update(kind="Inventory", cogs_account_id="cogs-id", asset_account_id="asset-id")
    path.write_text(json.dumps(raw))


@pytest.mark.parametrize("inventory", [False, True])
@pytest.mark.parametrize("taxable", [False, True])
@pytest.mark.parametrize("transport", ["direct-sdk", "qbwc"])
def test_full_transport_preparation_and_restart(
    commercial, tmp_path, inventory, taxable, transport
):
    path, token, payload = commercial
    if inventory:
        inventory_policy(commercial)
    if not taxable:
        raw = json.loads(path.read_text())
        raw["companies"]["company-a"]["invoice_masters"]["commercial"].update(
            tax_item_id=None, tax_rate="0"
        )
        path.write_text(json.dumps(raw))
        payload["tax_amount"] = "0.00"
    svc = DurableQBWCDiscoveryService.from_path(path)
    if transport == "direct-sdk":

        def exchange(request, destination):
            destination.write_text(response(request, taxable=taxable))

        assert (
            discover(
                svc,
                token,
                "connector-company-a",
                PASSWORD_A,
                "931",
                invoice_check=payload,
                exchange=exchange,
            )["compatibility"]
            == "matched"
        )
        evidence_id = "931"
    else:
        invoice_job(
            svc, token, "connector-company-a", "commercial-one", payload=payload, enqueue=True
        )
        ticket, _ = authenticate(svc)
        request = send(svc, ticket)
        assert receive(svc, ticket, response(request, taxable=taxable)) == 100
        call(svc, "closeConnection", ticket=ticket)
        assert (
            invoice_job(
                DurableQBWCDiscoveryService.from_path(path),
                token,
                "connector-company-a",
                "commercial-one",
            )["compatibility"]
            == "matched"
        )
        evidence_id = "commercial-one"
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        payload=payload,
        master_evidence={
            "transport": transport,
            "connector": "connector-company-a",
            "id": evidence_id,
        },
    )
    job = Bridge(path).prepare(token, "company-a", envelope)
    assert Bridge(path).action(token, "company-a", job["id"], "validate")["state"] == "validated"
    preview = Bridge(path).preview(token, "company-a", job["id"])
    assert preview["total"] == ("11.00" if taxable else "10.00")
    assert preview["tax_amount"] == ("1.00" if taxable else "0.00")
    assert preview["live_posting"] is False
    assert preview["scope"] == "unposted-invoice-preview"
    assert preview == Bridge(path).preview(token, "company-a", job["id"])


@pytest.mark.parametrize(
    "case",
    [
        "stock",
        "commitments",
        "cogs",
        "asset",
        "location",
        "serial",
        "bin",
        "missing-preferences",
        "uom",
        "price",
        "customer-price-level",
        "item-tax",
        "customer-tax",
        "taxability",
        "tax-rate",
        "tax-inclusive",
        "percentage",
        "duplicate-lines",
    ],
)
def test_incompatible_commercial_evidence_fails(commercial, case):
    path, _, payload = commercial
    inventory = case in (
        "stock",
        "commitments",
        "cogs",
        "asset",
        "location",
        "serial",
        "bin",
        "missing-preferences",
        "duplicate-lines",
    )
    if inventory:
        inventory_policy(commercial)
    if case == "duplicate-lines":
        payload["lines"] *= 3
        payload["tax_amount"] = "3.00"
    check = plan(Config.load(path).companies["company-a"], payload)
    svc = DurableQBWCDiscoveryService.from_path(path)
    request = append_queries(svc._discovery_request("941", "17.0"), "941", check)

    def mutate(rows):
        item = rows[("ItemInventory" if inventory else "ItemService", "service-id")]
        prefs = rows[("Preferences", None)]
        customer = rows[("Customer", "customer-id")]
        if case == "stock":
            item["QuantityOnHand"] = "1"
        elif case == "commitments":
            item["QuantityOnSalesOrder"] = "4"
        elif case == "cogs":
            rows[("Account", "cogs-id")]["AccountType"] = "Expense"
        elif case == "asset":
            item["AssetAccountRef"] = {"ListID": "wrong"}
        elif case == "location":
            prefs["MultiLocationInventoryPreferences"]["IsMultiLocationInventoryEnabled"] = "true"
        elif case == "serial":
            prefs["ItemsAndInventoryPreferences"]["IsTrackingSerialOrLotNumber"] = "SerialNumber"
        elif case == "bin":
            prefs["ItemsAndInventoryPreferences"]["IsRSBEnabled"] = "true"
        elif case == "missing-preferences":
            prefs.pop("ItemsAndInventoryPreferences")
        elif case == "uom":
            item["UnitOfMeasureSetRef"] = {"ListID": "unit"}
        elif case == "price":
            item["SalesOrPurchase"]["Price"] = "6.00"
        elif case == "percentage":
            item["SalesOrPurchase"]["PricePercent"] = "5"
        elif case == "customer-price-level":
            customer["PriceLevelRef"] = {"ListID": "discount"}
        elif case == "item-tax":
            item["SalesTaxCodeRef"] = {"ListID": "other"}
        elif case == "customer-tax":
            customer["SalesTaxCodeRef"] = {"ListID": "other"}
        elif case == "taxability":
            rows[("SalesTaxCode", "tax-code")]["IsTaxable"] = "false"
        elif case == "tax-rate":
            rows[("ItemSalesTax", "tax-item")]["TaxRate"] = "11"
        elif case == "tax-inclusive":
            item["IsTaxIncluded"] = "true"

    with pytest.raises(BridgeError):
        validate_response(response(request, mutate=mutate), "941", check)


@pytest.mark.parametrize(
    "case", ["quantity", "price", "amount", "tax", "missing-quantity", "missing-tax", "total-limit"]
)
def test_commercial_payload_fails_before_dispatch(commercial, case):
    path, _, payload = commercial
    line = payload["lines"][0]
    if case == "quantity":
        line["quantity"] = "NaN"
    elif case == "price":
        line["unit_price"] = "-5"
    elif case == "amount":
        line["amount"] = "11.00"
    elif case == "tax":
        payload["tax_amount"] = "2.00"
    elif case == "missing-quantity":
        line.pop("quantity")
    elif case == "missing-tax":
        payload.pop("tax_amount")
    else:
        raw = json.loads(path.read_text())
        raw["companies"]["company-a"]["max_total"] = "10.00"
        path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        plan(Config.load(path).companies["company-a"], payload)


@pytest.mark.parametrize("case", ["customer-code", "item-code", "taxable-code", "malformed-prefs"])
def test_disabled_sales_tax_does_not_bypass_master_evidence(commercial, case):
    path, _, payload = commercial
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["commercial"].update(
        tax_item_id=None, tax_rate="0"
    )
    path.write_text(json.dumps(raw))
    payload["tax_amount"] = "0.00"
    check = plan(Config.load(path).companies["company-a"], payload)
    svc = DurableQBWCDiscoveryService.from_path(path)
    request = append_queries(svc._discovery_request("961", "17.0"), "961", check)
    assert "ItemSalesTaxQueryRq" not in request

    def mutate(rows):
        if case == "customer-code":
            rows[("Customer", "customer-id")].pop("SalesTaxCodeRef")
        elif case == "item-code":
            rows[("ItemService", "service-id")].pop("SalesTaxCodeRef")
        elif case == "taxable-code":
            rows[("SalesTaxCode", "tax-code")]["IsTaxable"] = "true"
        else:
            rows[("Preferences", None)]["SalesTaxPreferences"] = "invalid"

    with pytest.raises(BridgeError):
        validate_response(response(request, taxable=False, mutate=mutate), "961", check)


def test_zero_rated_taxable_policy_still_requires_sales_tax_preferences(commercial):
    path, _, payload = commercial
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["commercial"]["tax_rate"] = "0"
    path.write_text(json.dumps(raw))
    payload["tax_amount"] = "0.00"
    check = plan(Config.load(path).companies["company-a"], payload)
    svc = DurableQBWCDiscoveryService.from_path(path)
    request = append_queries(svc._discovery_request("962", "17.0"), "962", check)
    assert "ItemSalesTaxQueryRq" in request

    def mutate(rows):
        rows[("Preferences", None)].pop("SalesTaxPreferences")
        rows[("ItemSalesTax", "tax-item")]["TaxRate"] = "0"

    with pytest.raises(BridgeError, match="tax preferences"):
        validate_response(response(request, mutate=mutate), "962", check)


@pytest.mark.parametrize("status", [1, 500])
def test_commercial_preview_empty_list_is_not_verified_tax_evidence(commercial, status):
    from kaydbooks_bridge.invoice_compatibility import preview_request, preview_response

    path, _, payload = commercial
    svc = DurableQBWCDiscoveryService.from_path(path)
    check = plan(Config.load(path).companies["company-a"], payload)
    exact = append_queries(svc._discovery_request("951", "17.0"), "951", check)
    records = {node.tag: node for node in ET.fromstring(response(exact)).find("QBXMLMsgsRs")}
    request = preview_request(svc._discovery_request("952", "17.0"), "952", commercial=True)
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRs")
    for query in ET.fromstring(request).find("QBXMLMsgsRq"):
        tag = query.tag.replace("Rq", "Rs")
        if tag in ("ItemInventoryQueryRs", "ItemSalesTaxQueryRs"):
            node = ET.Element(
                tag, statusCode=str(status), statusSeverity="Info" if status == 1 else "Error"
            )
        else:
            node = records[tag]
        node.set("requestID", query.attrib["requestID"])
        batch.append(node)
    xml = ET.tostring(root, encoding="unicode")
    if status == 1:
        _, masters = preview_response(xml, "952", commercial=True)
        assert masters["ItemSalesTax"] == masters["ItemInventory"] == []
    else:
        with pytest.raises(BridgeError):
            preview_response(xml, "952", commercial=True)
