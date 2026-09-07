"""Cash, discount and remaining balance must agree independently."""
# ruff: noqa: F811

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from kaydbooks_bridge import (
    customer_payments,
    dispatch,
    payment_receipt,
    supplier_payment_receipt,
    supplier_payments,
)
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from test_customer_payments import payment_case as customer_case  # noqa: F401
from test_customer_payments import records as customer_records
from test_customer_payments import response
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_sample_payments import Session as CustomerSession
from test_sample_supplier_payments import Session as SupplierSession
from test_supplier_payments import payment_case as supplier_case  # noqa: F401
from test_supplier_payments import records as supplier_records


@pytest.fixture(params=["customer", "supplier"])
def case(request):
    kind = request.param
    path, _, payload = request.getfixturevalue(kind + "_case")
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"].setdefault("account_roles", {})[kind + "_discount"] = (
        "discount-id"
    )
    path.write_text(json.dumps(raw))
    policy = Config.load(path).companies["company-a"]
    payload["allocations"][0].update(discount_amount="1.00", discount_account=kind + "_discount")
    module = customer_payments if kind == "customer" else supplier_payments
    receipt = payment_receipt if kind == "customer" else supplier_payment_receipt
    rows = customer_records() if kind == "customer" else supplier_records()
    rows["Account"].append(
        {
            "ListID": "discount-id",
            "IsActive": "true",
            "AccountType": "Income" if kind == "customer" else "Expense",
        }
    )
    return kind, policy, payload, module, receipt, rows


@pytest.mark.parametrize(
    "change", ["missing-amount", "missing-account", "negative", "zero", "fractional", "wrong-role"]
)
def test_invalid_or_unmapped_discount_cannot_prepare(case, change):
    kind, policy, payload, module, _, _ = case
    allocation = payload["allocations"][0]
    if change == "missing-amount":
        allocation.pop("discount_amount")
    elif change == "missing-account":
        allocation.pop("discount_account")
    elif change == "wrong-role":
        allocation["discount_account"] = "invoice_receivable"
    else:
        allocation["discount_amount"] = {
            "negative": "-1.00",
            "zero": "0.00",
            "fractional": "1.005",
        }[change]
    with pytest.raises(BridgeError):
        module.validate_payload(payload, policy)


@pytest.mark.parametrize("change", ["over-balance", "inactive", "wrong-type"])
def test_discount_checks_fresh_account_and_full_settlement(case, change):
    _, policy, payload, module, _, rows = case
    if change == "over-balance":
        payload["allocations"][0]["discount_amount"] = "6.00"
    elif change == "inactive":
        rows["Account"][-1]["IsActive"] = "false"
    else:
        rows["Account"][-1]["AccountType"] = "Bank"
    check = module.plan(policy, payload)
    request = module.append_check(S._discovery_request("1234", "17.0"), "1234", check)
    with pytest.raises(BridgeError):
        module.validate_check(response(request, rows), "1234", check)


def test_discount_value_counts_toward_company_and_schedule_limits(case):
    kind, policy, payload, module, _, _ = case
    job = {"operation": kind + "-payment.create", "payload": payload}
    assert dispatch.amount(job) == Decimal("6.00")
    with pytest.raises(BridgeError, match="company limit"):
        module.validate_payload(payload, replace(policy, max_total="5.00"))
    original = module.plan(policy, payload)["context_sha256"]
    changed = replace(
        policy, account_roles={**policy.account_roles, kind + "_discount": "changed-id"}
    )
    assert module.plan(changed, payload)["context_sha256"] != original


@pytest.mark.parametrize("change", ["amount", "account", "missing"])
def test_saved_discount_must_match_exact_requested_amount_and_account(case, change, tmp_path):
    kind, policy, payload, _, receipt, _ = case
    session = CustomerSession() if kind == "customer" else SupplierSession()
    session.discount = "1.00"
    write = receipt.add_request(policy, payload, "1234998")
    answer = session("<QBXML><QBXMLMsgsRq/></QBXML>", write, tmp_path, lambda _: True)
    operation = "ReceivePaymentAdd" if kind == "customer" else "BillPaymentCheckAdd"
    assert receipt.validate_receipt(answer, policy, payload, "1234998", operation=operation)[
        "settlement_discounts"
    ]
    if change == "amount":
        answer = answer.replace(
            "<DiscountAmount>1.00</DiscountAmount>", "<DiscountAmount>2.00</DiscountAmount>"
        )
    elif change == "account":
        answer = answer.replace("discount-id", "wrong-id")
    else:
        answer = answer.replace("<DiscountAmount>1.00</DiscountAmount>", "")
    with pytest.raises(BridgeError, match="discount"):
        receipt.validate_receipt(answer, policy, payload, "1234998", operation=operation)
