"""Synthetic master compatibility and durable native transport checks."""

import copy
import json
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import COMPANY_A, HOST, PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def setup_invoice(direct):  # noqa: F811
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"].append("validate")
    company = raw["companies"]["company-a"]
    customer, item = company["customers"][0], company["items"][0]
    company["account_roles"] = {"invoice_receivable": "ar-id"}
    company["invoice_masters"] = {
        "currency_id": "usd-id",
        "customers": {customer: "customer-id"},
        "items": {item: {"list_id": "service-id", "income_account_id": "income-id"}},
    }
    path.write_text(json.dumps(raw))
    payload = {
        "customer_id": customer,
        "currency": company["currency"],
        "txn_date": "2026-09-06",
        "ref_number": "SYN-CHECK",
        "lines": [{"item_id": item, "amount": "1.00"}],
    }
    return path, token, payload


def exchange(mutate=None):
    def send(request, destination):
        req = ET.fromstring(request).find("QBXMLMsgsRq")
        records = [
            {**HOST, "SupportedQBXMLVersion": ["17.0"]},
            COMPANY_A,
            {
                "MultiCurrencyPreferences": {
                    "IsMultiCurrencyOn": "true",
                    "HomeCurrencyRef": {"ListID": "usd-id"},
                }
            },
            {"ListID": "usd-id", "IsActive": "true", "CurrencyCode": "USD"},
            {
                "ListID": "ar-id",
                "IsActive": "true",
                "AccountType": "AccountsReceivable",
                "CurrencyRef": {"ListID": "usd-id"},
            },
            {"ListID": "customer-id", "IsActive": "true", "CurrencyRef": {"ListID": "usd-id"}},
            {
                "ListID": "service-id",
                "IsActive": "true",
                "SalesOrPurchase": {"AccountRef": {"ListID": "income-id"}},
            },
            {"ListID": "income-id", "IsActive": "true", "AccountType": "Income"},
        ]
        records = copy.deepcopy(records)
        if mutate:
            mutate(records)
        if len(req) == 7:
            if req[3].tag == "AccountQueryRq":
                records.pop(3)
            else:
                records = [records[i] for i in (0, 1, 2, 3, 5, 6, 4)]
        root = ET.Element("QBXML")
        batch = ET.SubElement(root, "QBXMLMsgsRs")

        def fields(parent, record):
            for k, v in record.items():
                for entry in v if isinstance(v, list) else [v]:
                    node = ET.SubElement(parent, k)
                    if isinstance(entry, dict):
                        fields(node, entry)
                    else:
                        node.text = entry

        for query, record in zip(req, records, strict=True):
            entity = query.tag.removesuffix("QueryRq")
            response = ET.SubElement(
                batch,
                entity + "QueryRs",
                requestID=query.attrib["requestID"],
                statusCode="0",
                statusSeverity="Info",
            )
            fields(ET.SubElement(response, entity + "Ret"), record)
        destination.write_text(ET.tostring(root, encoding="unicode"))

    return send


def run(setup, send=None, **kw):
    path, token, payload = setup
    return discover(
        DurableQBWCDiscoveryService.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "921",
        invoice_check=payload,
        exchange=send or exchange(),
        **kw,
    )


def test_success_recovery_and_context_immutability(setup_invoice):
    def crash(request, destination):
        assert len(ET.fromstring(request).find("QBXMLMsgsRq")) == 8
        exchange()(request, destination)
        raise RuntimeError("interrupted after response")

    with pytest.raises(RuntimeError):
        run(setup_invoice, crash)
    result = run(setup_invoice, lambda *_: pytest.fail("replayed query"))
    assert result["compatibility"] == "matched" and not result["live_posting"]
    assert result["scope"] == "master-evidence-only" and "accounts" not in result
    setup_invoice[2]["lines"][0]["amount"] = "2.00"
    with pytest.raises(BridgeError, match="ownership"):
        run(setup_invoice, lambda *_: pytest.fail("changed payload replay"))


@pytest.mark.parametrize(
    "case",
    [
        "home",
        "code",
        "ar-currency",
        "customer-currency",
        "inactive-customer",
        "inactive-item",
        "item-account",
        "income-type",
        "ar-type",
        "missing-currency",
        "ambiguous-sales",
        "wrong-company",
        "wrong-item",
    ],
)
def test_incompatible_masters_block(setup_invoice, case):
    def mutate(rows):
        if case == "home":
            rows[2]["MultiCurrencyPreferences"]["HomeCurrencyRef"]["ListID"] = "other"
        elif case == "code":
            rows[3]["CurrencyCode"] = "EUR"
        elif case == "ar-currency":
            rows[4]["CurrencyRef"]["ListID"] = "other"
        elif case == "customer-currency":
            rows[5]["CurrencyRef"]["ListID"] = "other"
        elif case == "inactive-customer":
            rows[5]["IsActive"] = "false"
        elif case == "inactive-item":
            rows[6]["IsActive"] = "false"
        elif case == "item-account":
            rows[6]["SalesOrPurchase"]["AccountRef"]["ListID"] = "other"
        elif case == "income-type":
            rows[7]["AccountType"] = "Expense"
        elif case == "ar-type":
            rows[4]["AccountType"] = "Bank"
        elif case == "missing-currency":
            rows[5].pop("CurrencyRef")
        elif case == "ambiguous-sales":
            rows[6]["SalesAndPurchase"] = {"IncomeAccountRef": {"ListID": "income-id"}}
        elif case == "wrong-company":
            rows[1]["EIN"] = "different"
        elif case == "wrong-item":
            rows[6]["ListID"] = "other"

    with pytest.raises(BridgeError, match="validation"):
        run(setup_invoice, exchange(mutate))
    with pytest.raises(BridgeError, match="blocked"):
        run(setup_invoice, lambda *_: pytest.fail("must not retry"))


def test_sales_and_purchase_service_item(setup_invoice):
    def mutate(rows):
        rows[6].pop("SalesOrPurchase")
        rows[6]["SalesAndPurchase"] = {"IncomeAccountRef": {"ListID": "income-id"}}

    assert run(setup_invoice, exchange(mutate))["compatibility"] == "matched"


@pytest.mark.parametrize("case", ["permission", "mapping", "currency", "unsupported-alias"])
def test_invalid_context_before_dispatch(setup_invoice, case):
    path, _, payload = setup_invoice
    raw = json.loads(path.read_text())
    if case == "permission":
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "validate"
        )
    elif case == "mapping":
        raw["companies"]["company-a"].pop("invoice_masters")
    elif case == "currency":
        payload["currency"] = "EUR"
    elif case == "unsupported-alias":
        payload["lines"][0]["item_id"] = "unknown"
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        run(setup_invoice, lambda *_: pytest.fail("must not dispatch"))


def test_unknown_mapping_fields_rejected(setup_invoice):
    path, _, _ = setup_invoice
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["query"] = "injected"
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        Config.load(path)


@pytest.mark.parametrize("case", ["match", "enabled", "unexpected-reference"])
def test_explicit_single_currency_mode(setup_invoice, case):
    path, _, _ = setup_invoice
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["invoice_masters"]["currency_id"] = None
    path.write_text(json.dumps(raw))

    def mutate(rows):
        rows[2]["MultiCurrencyPreferences"] = {
            "IsMultiCurrencyOn": "true" if case == "enabled" else "false"
        }
        rows[4].pop("CurrencyRef")
        if case != "unexpected-reference":
            rows[5].pop("CurrencyRef")

    if case == "match":
        result = run(setup_invoice, exchange(mutate))
        assert result["currency_basis"] == "configured-single-currency"
    else:
        with pytest.raises(BridgeError, match="validation"):
            run(setup_invoice, exchange(mutate))


@pytest.mark.parametrize("disabled", [True, False])
def test_master_preview_currency_unavailable_requires_disabled_preference(setup_invoice, disabled):
    path, token, _ = setup_invoice

    def respond(request, destination):
        def mutate(rows):
            rows[2]["MultiCurrencyPreferences"] = {
                "IsMultiCurrencyOn": "false" if disabled else "true"
            }

        exchange(mutate)(request, destination)
        root = ET.fromstring(destination.read_text())
        currency = root.find("QBXMLMsgsRs/CurrencyQueryRs")
        currency.remove(currency[0])
        currency.set("statusCode", "3250")
        currency.set("statusSeverity", "Error")
        destination.write_text(ET.tostring(root, encoding="unicode"))

    def call():
        return discover(
            DurableQBWCDiscoveryService.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            "922",
            master_preview=True,
            exchange=respond,
        )

    if disabled:
        result = call()
        assert result["masters"]["Currency"] == [] and not result["complete"]
    else:
        with pytest.raises(BridgeError, match="validation"):
            call()


@pytest.mark.parametrize("case", ["correlation", "warning", "duplicate", "missing"])
def test_malformed_master_response(setup_invoice, case):
    def respond(request, destination):
        exchange()(request, destination)
        root = ET.fromstring(destination.read_text())
        customer = root.find("QBXMLMsgsRs/CustomerQueryRs")
        if case == "correlation":
            customer.set("requestID", "999")
        elif case == "warning":
            customer.set("statusSeverity", "Warn")
        elif case == "duplicate":
            customer.append(copy.deepcopy(customer[0]))
        else:
            customer.remove(customer[0])
        destination.write_text(ET.tostring(root, encoding="unicode"))

    with pytest.raises(BridgeError, match="validation"):
        run(setup_invoice, respond)


@pytest.mark.parametrize("change", ["policy", "permission"])
def test_replay_revalidates_policy_and_permission(setup_invoice, change):
    run(setup_invoice)
    path, _, _ = setup_invoice
    raw = json.loads(path.read_text())
    if change == "policy":
        raw["companies"]["company-a"]["invoice_masters"]["currency_id"] = "another-currency"
    else:
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "validate"
        )
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        run(setup_invoice, lambda *_: pytest.fail("must not repeat"))
