"""Verify customer percentage pricing against exact live master response contracts."""

# ruff: noqa: F401,F811
import json
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.invoice_compatibility import append_queries, plan, validate_response
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from test_direct_sdk import direct
from test_invoice_commercial import commercial, response
from test_invoice_compatibility import setup_invoice
from test_qbwc_discovery import discovery_setup


@pytest.fixture
def fixed_case(commercial):
    path, token, payload = commercial
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["commercial"].update(
        tax_item_id=None,
        tax_rate="0",
        pricing="fixed-percentage",
        price_level={"list_id": "price-level-id", "percentage": "-25"},
    )
    path.write_text(json.dumps(raw))
    payload["tax_amount"] = "0.00"
    payload["lines"][0].update(quantity="14.08", unit_price="3.75", amount="52.80")
    return path, payload


def make_response(request, mutate=None):
    def masters(rows):
        rows[("Customer", "customer-id")]["PriceLevelRef"] = {"ListID": "price-level-id"}
        rows[("Preferences", None)]["SalesAndCustomersPreferences"] = {
            "PriceLevels": {"IsUsingPriceLevels": "true"}
        }
        rows[("PriceLevel", "price-level-id")] = {
            "ListID": "price-level-id",
            "IsActive": "true",
            "PriceLevelType": "FixedPercentage",
            "PriceLevelFixedPercentage": "-25",
        }
        if mutate:
            mutate(rows)

    return response(request, taxable=False, mutate=masters)


def checked(fixed_case, mutate=None):
    path, payload = fixed_case
    policy = Config.load(path).companies["company-a"]
    check = plan(policy, payload)
    request = append_queries(S._discovery_request("931", "17.0"), "931", check)
    validate_response(make_response(request, mutate), "931", check)
    return check, request


def test_verified_discount_and_fractional_quantity(fixed_case):
    check, request = checked(fixed_case)
    q = ET.fromstring(request).find(".//PriceLevelQueryRq")
    assert q.findtext("ListID") == "price-level-id"
    assert q.find("MaxReturned") is None
    assert check["invoice"]["lines"][0]["amount"] == "52.80"


@pytest.mark.parametrize(
    "field,value",
    [
        ("IsActive", "false"),
        ("ListID", "other"),
        ("PriceLevelType", "PerItem"),
        ("PriceLevelFixedPercentage", "-20"),
        ("PriceLevelFixedPercentage", "NaN"),
        ("CurrencyRef", {"ListID": "foreign"}),
    ],
)
def test_changed_or_unsupported_price_level_is_denied(fixed_case, field, value):
    with pytest.raises(BridgeError):
        checked(
            fixed_case, lambda rows: rows[("PriceLevel", "price-level-id")].update({field: value})
        )


def test_unreviewed_customer_price_level_is_denied(fixed_case):
    with pytest.raises(BridgeError, match="customer price level differs"):
        checked(
            fixed_case,
            lambda rows: rows[("Customer", "customer-id")].update(
                PriceLevelRef={"ListID": "other"}
            ),
        )


@pytest.mark.parametrize(
    "preferences", [{}, {"PriceLevels": {"IsUsingPriceLevels": "false"}}, None]
)
def test_unverified_price_level_preferences_are_denied(fixed_case, preferences):
    with pytest.raises(BridgeError, match="preferences"):
        checked(
            fixed_case,
            lambda rows: rows[("Preferences", None)].update(
                SalesAndCustomersPreferences=preferences
            ),
        )


def test_undiscounted_rate_is_denied(fixed_case):
    fixed_case[1]["lines"][0].update(quantity="10.56", unit_price="5.00", amount="52.80")
    with pytest.raises(BridgeError, match="unit price"):
        checked(fixed_case)


def test_list_price_policy_still_rejects_price_levels(fixed_case):
    path, payload = fixed_case
    raw = json.loads(path.read_text())
    policy = raw["companies"]["company-a"]["invoice_masters"]["commercial"]
    policy["pricing"] = "list-price"
    policy.pop("price_level")
    path.write_text(json.dumps(raw))
    payload["lines"][0].update(quantity="10.56", unit_price="5.00", amount="52.80")
    with pytest.raises(BridgeError, match="price levels are not qualified"):
        checked(fixed_case)


def test_policy_change_invalidates_evidence_context(fixed_case):
    before, _ = checked(fixed_case)
    path, payload = fixed_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["commercial"]["price_level"]["percentage"] = (
        "-20"
    )
    path.write_text(json.dumps(raw))
    assert (
        plan(Config.load(path).companies["company-a"], payload)["context_sha256"]
        != before["context_sha256"]
    )


@pytest.mark.parametrize("percentage", ["-100", "101", "NaN", "-25.0000001"])
def test_invalid_configured_percentage_is_denied(fixed_case, percentage):
    path, payload = fixed_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["commercial"]["price_level"]["percentage"] = (
        percentage
    )
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        plan(Config.load(path).companies["company-a"], payload)


def test_fractional_rate_needing_rounding_is_denied(fixed_case):
    with pytest.raises(BridgeError, match="unsupported rounding"):
        checked(
            fixed_case,
            lambda rows: rows[("ItemService", "service-id")]["SalesOrPurchase"].update(
                Price="5.000001"
            ),
        )


def test_inventory_discount_is_not_silently_enabled(fixed_case):
    path, payload = fixed_case
    raw = json.loads(path.read_text())
    item = next(iter(raw["companies"]["company-a"]["invoice_masters"]["items"].values()))
    item.update(kind="Inventory", cogs_account_id="cogs-id", asset_account_id="asset-id")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="unadjusted service"):
        plan(Config.load(path).companies["company-a"], payload)
