"""Reviewed expense-account bill adjustments; inventory cost allocation is separate."""

from decimal import Decimal

from .config import BridgeError, identifier, strict_keys
from .validation import money


def validate(payload, policy):
    if "adjustments" not in payload:
        return
    entries = payload["adjustments"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise BridgeError("bill adjustments require 1-20 explicit entries")
    if len(payload["lines"]) + len(entries) > 100:
        raise BridgeError("bill plus adjustments exceeds 100 native lines")
    if any(
        policy.bill_masters.get("items", {}).get(line.get("item_id"), {}).get("type") == "inventory"
        for line in payload["lines"]
    ):
        raise BridgeError("inventory bill adjustments require qualified cost allocation")
    gross = sum(money(line["amount"]) for line in payload["lines"])
    discounts = set()
    charges = Decimal(0)
    for entry in entries:
        strict_keys(entry, {"kind", "scope", "expense_id", "amount"}, {"line_number"})
        if entry["kind"] not in ("discount", "charge") or entry["scope"] not in (
            "line",
            "document",
        ):
            raise BridgeError("explicit fixed bill adjustment kind and scope required")
        identifier(entry["expense_id"])
        if entry["expense_id"] not in policy.bill_masters["expenses"]:
            raise BridgeError("adjustment expense account is not mapped for this company")
        value = money(entry["amount"])
        number = entry.get("line_number")
        if entry["scope"] == "line":
            if type(number) is not int or not 1 <= number <= len(payload["lines"]):
                raise BridgeError("bill adjustment requires an existing original line number")
        elif "line_number" in entry:
            raise BridgeError("document bill adjustment cannot specify a line number")
        if entry["kind"] == "discount":
            if number in discounts or (discounts and (number is None or None in discounts)):
                raise BridgeError(
                    "use one document bill discount or one discount per selected line"
                )
            discounts.add(number)
            limit = gross if number is None else money(payload["lines"][number - 1]["amount"])
            if value > limit:
                raise BridgeError("bill discount exceeds its selected basis")
        else:
            charges += value
    if subtotal(payload) <= 0 or gross + charges > money(policy.max_total):
        raise BridgeError("bill adjustments require positive net and bounded gross total")


def subtotal(payload):
    return sum((Decimal(line["amount"]) for line in payload["lines"]), Decimal(0)) + sum(
        (
            Decimal(entry["amount"]) * (-1 if entry["kind"] == "discount" else 1)
            for entry in payload.get("adjustments", [])
        ),
        Decimal(0),
    )


def expense_lines(payload):
    return [
        {
            "expense_id": entry["expense_id"],
            "amount": ("-" if entry["kind"] == "discount" else "") + entry["amount"],
            "memo": (
                "Line " + str(entry["line_number"]) if entry["scope"] == "line" else "Document"
            )
            + " "
            + entry["kind"],
        }
        for entry in payload.get("adjustments", [])
    ]
