import copy
import json
import time

import pytest

from kaydbooks_bridge.bill_lookup import append_preview, validate_preview
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import COMPANY_A, HOST, PASSWORD_A, direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401


@pytest.fixture
def exact_case(direct):  # noqa: F811
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = ["read", "validate", "prepare", "submit"]
    raw["companies"]["company-a"]["bill_masters"] = {
        "vendors": {"supplier": "V-A"},
        "payable": "AP-A",
        "expenses": {"office": "E-A"},
    }
    path.write_text(json.dumps(raw))
    payload = {
        "vendor_id": "supplier",
        "ref_number": "BILL-001",
        "txn_date": "2026-09-06",
        "due_date": "2026-10-06",
        "currency": "USD",
        "lines": [{"expense_id": "office", "amount": "10.00"}],
    }
    return path, token, payload


def exact_response(request):
    from xml.etree import ElementTree as ET

    raw = FakeQuickBooks(
        entities={
            "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
            "Company": [COMPANY_A],
            "Preferences": [{"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}}],
            "StandardTerms": [
                {
                    "ListID": "T-A",
                    "Name": "Net 30",
                    "IsActive": "true",
                    "StdDueDays": "30",
                    "StdDiscountDays": "0",
                    "DiscountPct": "0.00",
                }
            ],
            "Vendor": [{"ListID": "V-A", "Name": "Synthetic Supplier", "IsActive": "true"}],
            "ItemService": [
                {
                    "ListID": "I-A",
                    "Name": "Synthetic purchased service",
                    "IsActive": "true",
                    "SalesAndPurchase": {
                        "ExpenseAccountRef": {"ListID": "E-A"},
                        "PurchaseCost": "2.50",
                    },
                }
            ],
            "Account": [
                {
                    "ListID": "AP-A",
                    "FullName": "Synthetic AP",
                    "IsActive": "true",
                    "AccountType": "AccountsPayable",
                },
                {
                    "ListID": "E-A",
                    "FullName": "Synthetic Expense",
                    "IsActive": "true",
                    "AccountType": "Expense",
                },
            ],
        }
    )(request)
    root = ET.fromstring(raw)
    for query, result in zip(ET.fromstring(request)[0], root[0], strict=True):
        selected = query.findtext("ListID")
        if selected is not None:
            for record in list(result):
                if record.findtext("ListID") != selected:
                    result.remove(record)
    return ET.tostring(root, encoding="unicode")


def exact_run(case, run="993", exchange=None):
    path, token, payload = case
    return discover(
        DurableQBWCDiscoveryService.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        run,
        bill_check=payload,
        exchange=exchange or (lambda rq, dest: dest.write_text(exact_response(rq))),
    )


def test_exact_bill_evidence_is_owned_fresh_and_payload_bound(exact_case):
    from kaydbooks_bridge.bill_evidence import resolve
    from kaydbooks_bridge.config import Config
    from kaydbooks_bridge.store import Store

    path, token, payload = exact_case
    assert exact_run(exact_case)["compatibility"] == "matched"
    config = Config.load(path)
    policy = config.companies["company-a"]
    store = Store(config.root, "company-a")
    ref = {"transport": "direct-sdk", "connector": "connector-company-a", "id": "993"}
    actor = config.authenticate(token)
    with store.transaction() as db:
        proof = resolve(config, policy, store, db, actor, payload, ref, time.time())
        assert proof["identity_sha256"] == config.connectors[ref["connector"]].identity_sha256
        changed = {**payload, "ref_number": "CHANGED"}
        with pytest.raises(BridgeError, match="exact bill"):
            resolve(config, policy, store, db, actor, changed, ref, time.time())
        with pytest.raises(BridgeError, match="stale"):
            resolve(
                config,
                policy,
                store,
                db,
                actor,
                payload,
                ref,
                proof["observed_at"] + policy.invoice_evidence_max_age_seconds,
            )
    assert (
        exact_run(exact_case, exchange=lambda *_: pytest.fail("duplicate read"))["state"]
        == "verified"
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("AccountsPayable", "Bank"),
        ("Expense", "Income"),
        ("IsActive>true", "IsActive>false"),
        ("IsMultiCurrencyOn>false", "IsMultiCurrencyOn>true"),
        ('requestID="9934"', 'requestID="9938"'),
    ],
)
def test_exact_bill_rejects_incompatible_masters(exact_case, old, new):
    def exchange(request, dest):
        raw = exact_response(request)
        assert old in raw
        dest.write_text(raw.replace(old, new))

    with pytest.raises(BridgeError, match="validation failed"):
        exact_run(exact_case, exchange=exchange)


def test_preview_cannot_be_reused_as_bill_evidence(exact_case):
    from kaydbooks_bridge.bill_evidence import resolve
    from kaydbooks_bridge.store import Store

    path, token, payload = exact_case
    service = DurableQBWCDiscoveryService.from_path(path)
    discover(
        service,
        token,
        "connector-company-a",
        PASSWORD_A,
        "994",
        bill_preview=True,
        exchange=lambda request, dest: dest.write_text(response(request)),
    )
    config = service.config
    store = Store(config.root, "company-a")
    with store.transaction() as db, pytest.raises(BridgeError, match="exact bill"):
        resolve(
            config,
            config.companies["company-a"],
            store,
            db,
            config.authenticate(token),
            payload,
            {"transport": "direct-sdk", "connector": "connector-company-a", "id": "994"},
            time.time(),
        )


@pytest.mark.parametrize(
    "due,discount,valid",
    [("2026-10-06", "0.00", True), ("2026-10-05", "0.00", False), ("2026-10-06", "1.00", False)],
)
def test_standard_terms_require_matching_due_date_without_discount(
    exact_case, due, discount, valid
):
    path, _, payload = exact_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["terms"] = {"net30": "T-A"}
    path.write_text(json.dumps(raw))
    payload.update(terms_id="net30", due_date=due)

    def exchange(request, dest):
        value = exact_response(request)
        assert "<StdDueDays>30</StdDueDays>" in value
        dest.write_text(value.replace("<DiscountPct>0.00", "<DiscountPct>" + discount))

    if valid:
        assert exact_run(exact_case, exchange=exchange)["state"] == "verified"
    else:
        with pytest.raises(BridgeError, match="validation failed"):
            exact_run(exact_case, exchange=exchange)


def response(request):
    return FakeQuickBooks(
        entities={
            "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
            "Company": [COMPANY_A],
            "Preferences": [{"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}}],
            "Vendor": [{"ListID": "V-A", "Name": "Synthetic Supplier", "IsActive": "true"}],
            "Account": [
                {
                    "ListID": "AP-A",
                    "FullName": "Synthetic AP",
                    "IsActive": "true",
                    "AccountType": "AccountsPayable",
                }
            ],
        }
    )(request)


def test_bill_preview_persists_and_replays_without_query(direct):  # noqa: F811
    path, token = direct

    def exchange(request, destination):
        assert "BillAdd" not in request and "<MaxReturned>20</MaxReturned>" in request
        destination.write_text(response(request))

    service = DurableQBWCDiscoveryService.from_path(path)
    result = discover(
        service,
        token,
        "connector-company-a",
        PASSWORD_A,
        "991",
        bill_preview=True,
        exchange=exchange,
    )
    assert result["state"] == "verified" and result["operation"] == "bill-master-preview"
    assert result["masters"]["Vendor"][0]["ListID"] == "V-A"
    assert result["complete"] is False and result["live_posting"] is False
    assert (
        discover(
            DurableQBWCDiscoveryService.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            "991",
            bill_preview=True,
            exchange=lambda *_: pytest.fail("replay query"),
        )
        == result
    )


@pytest.mark.parametrize(
    "old,new",
    [("IsActive>true", "IsActive>false"), ("9914", "9918"), ('statusCode="0"', 'statusCode="1"')],
)
def test_bill_preview_rejects_invalid_response(old, new):
    request = append_preview(DurableQBWCDiscoveryService._discovery_request("991", "17.0"), "991")
    raw = response(request)
    assert old in raw
    with pytest.raises(BridgeError):
        validate_preview(raw.replace(old, new), "991")


def test_bill_preview_rejects_missing_or_oversized_supplier_set():
    request = append_preview(DurableQBWCDiscoveryService._discovery_request("991", "17.0"), "991")
    from xml.etree import ElementTree as ET

    root = ET.fromstring(response(request))
    node = root[0][3]
    record = node[0]
    for index in range(20):
        other = copy.deepcopy(record)
        other.find("ListID").text = f"V-{index}"
        node.append(other)
    with pytest.raises(BridgeError, match="limit"):
        validate_preview(ET.tostring(root), "991")


def test_bill_preview_cannot_be_combined_with_other_mode(direct):  # noqa: F811
    path, token = direct
    with pytest.raises(BridgeError, match="cannot be combined"):
        discover(
            DurableQBWCDiscoveryService.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            "992",
            bill_preview=True,
            accounts=True,
            exchange=lambda *_: pytest.fail("query"),
        )
