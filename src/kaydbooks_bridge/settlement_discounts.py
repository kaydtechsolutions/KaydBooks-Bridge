"""Explicit payment discounts, separate from cash and never inferred from terms."""

from decimal import Decimal
from xml.etree import ElementTree as ET

from .config import BridgeError, strict_keys
from .invoice_commercial import decimal_evidence
from .validation import money

ROLES = {"customer": ("customer_discount", "Income"), "supplier": ("supplier_discount", "Expense")}


def amount(allocation):
    return money(allocation["discount_amount"]) if "discount_amount" in allocation else Decimal(0)


def settled(allocation):
    return money(allocation["amount"]) + amount(allocation)


def validate(allocation, policy, kind):
    strict_keys(allocation, {"txn_id", "amount"}, {"discount_amount", "discount_account"})
    if "discount_amount" in allocation or "discount_account" in allocation:
        strict_keys(allocation, {"txn_id", "amount", "discount_amount", "discount_account"})
        role, _ = ROLES[kind]
        if allocation["discount_account"] != role or role not in policy.account_roles:
            raise BridgeError("explicit configured settlement discount account required")
        amount(allocation)


def binding(policy, payload, kind):
    if any("discount_amount" in row for row in payload["allocations"]):
        role, _ = ROLES[kind]
        return {"discount_account": policy.account_roles[role]}
    return {}


def check_account(rows, check, kind):
    """Remove the optional final exact account after normal selector checks."""
    if "discount_account" in check["binding"]:
        record = rows.pop()
        if (
            record.get("ListID") != check["binding"]["discount_account"]
            or record.get("AccountType") != ROLES[kind][1]
            or record.get("IsActive") != "true"
            or record.get("CurrencyRef")
        ):
            raise BridgeError("settlement discount account is inactive or incompatible")


def append(node, allocation, check):
    if "discount_amount" in allocation:
        ET.SubElement(node, "DiscountAmount").text = allocation["discount_amount"]
        ET.SubElement(ET.SubElement(node, "DiscountAccountRef"), "ListID").text = check["binding"][
            "discount_account"
        ]


def verify(saved, requested, check):
    expected = amount(requested)
    if decimal_evidence(saved.get("DiscountAmount", "0")) != expected:
        raise BridgeError("saved settlement discount amount differs")
    if any(key in saved for key in ("DiscountClassRef", "TxnLineDetail")):
        raise BridgeError("unsupported settlement discount details")
    if expected:
        if (
            not isinstance(saved.get("DiscountAccountRef"), dict)
            or saved["DiscountAccountRef"].get("ListID") != check["binding"]["discount_account"]
        ):
            raise BridgeError("saved settlement discount account differs")
    elif "DiscountAccountRef" in saved:
        raise BridgeError("unexpected settlement discount account")


def receipt(payload, check):
    values = {
        row["txn_id"]: {
            "amount": row["discount_amount"],
            "account_list_id": check["binding"]["discount_account"],
        }
        for row in payload["allocations"]
        if "discount_amount" in row
    }
    return {"settlement_discounts": values} if values else {}
