"""Explicit fixed invoice adjustments and deterministic, cent-exact line placement."""

from decimal import Decimal

from .config import BridgeError, identifier, strict_keys


def validate(payload, company):
    from .validation import money

    adjustments = payload.get("adjustments", [])
    if "adjustments" not in payload:
        return
    if not isinstance(adjustments, list) or not 1 <= len(adjustments) <= 20:
        raise BridgeError("invoice adjustments require 1-20 explicit entries")
    if payload.get("tax_amount") != "0.00":
        raise BridgeError("invoice adjustments require explicit non-tax treatment")
    discounts = set()
    gross = sum(money(line["amount"]) for line in payload["lines"])
    charges = Decimal(0)
    for entry in adjustments:
        strict_keys(entry, {"kind", "scope", "item_id", "amount"}, {"line_number"})
        if entry["kind"] not in ("discount", "charge") or entry["scope"] not in (
            "line",
            "document",
        ):
            raise BridgeError("fixed adjustment kind and scope required")
        identifier(entry["item_id"])
        if entry["item_id"] not in company.items:
            raise BridgeError("adjustment item is not in the company allowlist")
        amount = money(entry["amount"])
        target = entry.get("line_number")
        if entry["scope"] == "line":
            if type(target) is not int or not 1 <= target <= len(payload["lines"]):
                raise BridgeError("adjustment line number must identify an existing item line")
        elif "line_number" in entry:
            raise BridgeError("document adjustment cannot specify a line number")
        if entry["kind"] == "discount":
            if target in discounts or (discounts and (target is None or None in discounts)):
                raise BridgeError("use one document discount or one discount per selected line")
            discounts.add(target)
            limit = gross if target is None else money(payload["lines"][target - 1]["amount"])
            if amount > limit:
                raise BridgeError("discount exceeds its selected item amount")
        else:
            charges += amount
    # A discount cannot hide gross exposure from a company's posting limit.
    if gross + charges > money(company.max_total) or subtotal(payload) <= 0:
        raise BridgeError("adjusted invoice requires positive net and bounded gross total")


def subtotal(payload):
    return sum((Decimal(line["amount"]) for line in payload["lines"]), Decimal(0)) + sum(
        (
            Decimal(a["amount"]) * (-1 if a["kind"] == "discount" else 1)
            for a in payload.get("adjustments", [])
        ),
        Decimal(0),
    )


def _shares(lines, amount):
    """Largest remainder allocation; original line order breaks ties deterministically."""
    weights = [int(Decimal(line["amount"]) * 100) for line in lines]
    cents, total = int(Decimal(amount) * 100), sum(weights)
    parts = [divmod(cents * weight, total) for weight in weights]
    shares = [part[0] for part in parts]
    order = sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))
    for index in order[: cents - sum(shares)]:
        shares[index] += 1
    return [format(Decimal(value) / 100, ".2f") for value in shares]


def native_lines(payload):
    """Map reviewed scopes to ordinary US InvoiceLineAdd items, never OE-only fields."""
    before = [[] for _ in payload["lines"]]
    after = [[] for _ in payload["lines"]]
    tail = []
    for entry in payload.get("adjustments", []):
        discount = entry["kind"] == "discount"
        targets = (
            range(len(before))
            if discount and entry["scope"] == "document"
            else [entry["line_number"] - 1]
            if entry["scope"] == "line"
            else []
        )
        amounts = (
            _shares(payload["lines"], entry["amount"])
            if discount and entry["scope"] == "document"
            else [entry["amount"]]
        )
        if not targets:
            tail.append({**entry, "adjustment": True})
            continue
        for index, amount in zip(targets, amounts, strict=True):
            if Decimal(amount) == 0:
                continue
            line = {
                **entry,
                "amount": ("-" if discount else "") + amount,
                "adjustment": True,
                "line_number": index + 1,
            }
            (before if discount else after)[index].append(line)
    result = []
    for index, line in enumerate(payload["lines"]):
        result.extend([line, *before[index], *after[index]])
    return [*result, *tail]
