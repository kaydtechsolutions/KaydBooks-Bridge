"""Base-currency expense-bill policy and simulation contracts; no native writes."""

from datetime import date
from decimal import Decimal

from .config import BridgeError, identifier, strict_keys
from .validation import canonical, digest, money


def validate_masters(value):
    if value == {}:
        return {}
    strict_keys(value, {"vendors", "payable", "expenses"}, {"terms"})
    from .account_lookup import validate_list_id

    if value["payable"] is None:
        raise BridgeError("bill payable requires an explicit ListID")
    validate_list_id(value["payable"])
    kinds = ("vendors", "expenses") + (("terms",) if "terms" in value else ())
    for kind in kinds:
        if not isinstance(value[kind], dict) or not 1 <= len(value[kind]) <= 1000:
            raise BridgeError("nonempty bounded bill master mappings required")
        for alias, list_id in value[kind].items():
            identifier(alias)
            if list_id is None:
                raise BridgeError("bill master requires an explicit ListID")
            validate_list_id(list_id)
    if value["payable"] in value["expenses"].values():
        raise BridgeError("payable and expense account mappings must differ")
    return {
        "payable": value["payable"],
        **{k: dict(value[k]) for k in kinds},
    }


def validate_payload(payload, policy):
    strict_keys(
        payload,
        {"vendor_id", "txn_date", "due_date", "ref_number", "currency", "lines"},
        {"terms_id"},
    )
    masters = validate_masters(policy.bill_masters)
    if not masters:
        raise BridgeError("bill master policy is not configured")
    identifier(payload["vendor_id"])
    if payload["vendor_id"] not in masters["vendors"]:
        raise BridgeError("vendor is not in the company bill allowlist")
    if "terms_id" in payload:
        identifier(payload["terms_id"])
        if payload["terms_id"] not in masters.get("terms", {}):
            raise BridgeError("terms are not in the company bill allowlist")
    import re

    dates = []
    for key in ("txn_date", "due_date"):
        value = payload[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise BridgeError("bill dates must be YYYY-MM-DD")
        try:
            dates.append(date.fromisoformat(value))
        except ValueError as exc:
            raise BridgeError("invalid bill date") from exc
    if dates[1] < dates[0]:
        raise BridgeError("bill due date precedes transaction date")
    if not isinstance(payload["ref_number"], str) or not re.fullmatch(
        r"[A-Za-z0-9-]{1,11}", payload["ref_number"]
    ):
        raise BridgeError("bill reference requires 1-11 letters, digits or hyphens")
    if payload["currency"] != policy.currency:
        raise BridgeError("bill currency must match configured base currency")
    lines = payload["lines"]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise BridgeError("bill requires 1-100 expense lines")
    total = Decimal("0")
    for line in lines:
        strict_keys(line, {"expense_id", "amount"})
        identifier(line["expense_id"])
        if line["expense_id"] not in masters["expenses"]:
            raise BridgeError("expense account is not in the company bill allowlist")
        total += money(line["amount"])
    if total > money(policy.max_total):
        raise BridgeError("company total limit exceeded")
    import json

    return json.loads(canonical(payload))


def context(policy, payload):
    bill = validate_payload(payload, policy)
    return {
        "schema": "bill-policy-context-v1",
        "vendor_list_id": policy.bill_masters["vendors"][bill["vendor_id"]],
        "payable_list_id": policy.bill_masters["payable"],
        "expense_list_ids": [
            policy.bill_masters["expenses"][line["expense_id"]] for line in bill["lines"]
        ],
        "currency": policy.currency,
        **(
            {"terms_list_id": policy.bill_masters["terms"][bill["terms_id"]]}
            if "terms_id" in bill
            else {}
        ),
    }


def require_context(config, policy, store, db, job, now):
    if job.get("bill_context") != context(policy, job["payload"]):
        raise BridgeError("bill master mapping changed; original context must be preserved")
    if not store.verify_audit(db):
        raise BridgeError("bill audit is invalid")
    saved = job.get("master_evidence")
    if saved is not None:
        from .bill_evidence import resolve

        if (
            resolve(
                config, policy, store, db, job["submitter"], job["payload"], saved["reference"], now
            )
            != saved
        ):
            raise BridgeError("linked bill evidence changed")


def preview(policy, job):
    binding = context(policy, job["payload"])
    result = {
        "schema": "bill-review-v1",
        "scope": "review-only-expense-bill",
        "job": job["id"],
        "company": policy.id,
        "fingerprint": job["fingerprint"],
        "payload": job["payload"],
        "configured_masters": binding,
        "master_evidence_verified": job.get("master_evidence") is not None,
        "controlled_sample_gate_configured": bool(policy.sample_bill_posting),
        "posting_authorized_by_preview": False,
        "live_posting": False,
        "total": format(sum(Decimal(line["amount"]) for line in job["payload"]["lines"]), ".2f"),
    }
    return {**result, "preview_sha256": digest(result)}
