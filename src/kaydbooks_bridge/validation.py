"""Narrow synthetic invoice schema; never accepts raw qbXML, SQL, or instructions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal

from .config import BridgeError, Company, identifier, strict_keys


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def money(value: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}", value):
        raise BridgeError("amount must be a positive decimal string with two places")
    amount = Decimal(value)
    if amount <= 0:
        raise BridgeError("amount must be positive")
    return amount


def validate_invoice(payload: dict, company: Company) -> dict:
    strict_keys(payload, {"customer_id", "txn_date", "ref_number", "currency", "lines"})
    identifier(payload["customer_id"])
    if payload["customer_id"] not in company.customers:
        raise BridgeError("customer is not in the company master allowlist")
    if not isinstance(payload["txn_date"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", payload["txn_date"]
    ):
        raise BridgeError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(payload["txn_date"])
    except ValueError as exc:
        raise BridgeError("invalid transaction date") from exc
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,11}", payload["ref_number"]
    ):
        raise BridgeError("reference must contain 1-11 letters, digits or hyphens")
    if payload["currency"] != company.currency:
        raise BridgeError("base currency mismatch; multicurrency is not implemented")
    if not isinstance(payload["lines"], list) or not 1 <= len(payload["lines"]) <= 100:
        raise BridgeError("invoice requires 1-100 lines")
    total = Decimal("0")
    for line in payload["lines"]:
        strict_keys(line, {"item_id", "amount"})
        identifier(line["item_id"])
        if line["item_id"] not in company.items:
            raise BridgeError("item is not in the company master allowlist")
        total += money(line["amount"])
    if total > money(company.max_total):
        raise BridgeError("company total limit exceeded")
    return json.loads(canonical(payload))


def validate_source(source: dict, company: Company) -> dict:
    strict_keys(source, {"namespace", "reference", "sha256", "original_values", "uncertain_fields"})
    if source["namespace"] not in company.sources:
        raise BridgeError("source is not authorized for this company")
    identifier(source["reference"])
    if not isinstance(source["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
        raise BridgeError("source content digest required")
    if (
        not isinstance(source["original_values"], dict)
        or len(canonical(source["original_values"])) > 65536
    ):
        raise BridgeError("bounded original source values required")
    if not isinstance(source["uncertain_fields"], list):
        raise BridgeError("uncertain_fields must be a list")
    # Original values are evidence, including hostile text. They are never executed.
    return json.loads(canonical(source))
