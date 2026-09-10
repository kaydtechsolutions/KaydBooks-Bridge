"""Expiring, exact-payload sample approvals delegated explicitly by an operator.

This does not dispatch, replace evidence, or grant production permissions.
Delegated approvals are recorded as automation, never as a human reviewer.
"""

import copy
import json
import math
import re

from .config import BridgeError, identifier, strict_keys
from .validation import digest

GATES = {
    "bill.create": ("sample_bill_posting", "max_bills"),
    "sales-receipt.create": ("sample_sales_receipt_posting", "max_receipts"),
    "journal.create": ("sample_journal_posting", "max_entries"),
    "customer-payment.create": ("sample_payment_posting", "max_payments"),
    "supplier-payment.create": ("sample_supplier_payment_posting", "max_payments"),
}


def validate_grant(principal, actor):
    grant = principal["sample_qualification"]
    strict_keys(
        grant,
        {
            "id",
            "company",
            "connector",
            "identity_sha256",
            "preparer",
            "enabled",
            "activated_at",
            "expires_at",
            "authorization",
            "entries",
        },
    )
    for name in ("id", "company", "connector", "preparer"):
        identifier(grant[name])
    if (
        principal.get("owner", False)
        or actor == grant["preparer"]
        or principal["companies"] != {grant["company"]: ["read", "approve"]}
        or type(grant["enabled"]) is not bool
        or not isinstance(grant["authorization"], str)
        or not 20 <= len(grant["authorization"]) <= 2000
        or not isinstance(grant["identity_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", grant["identity_sha256"])
        or grant["identity_sha256"] == "0" * 64
    ):
        raise BridgeError("invalid bounded sample qualification grant")
    start, end = grant["activated_at"], grant["expires_at"]
    if (
        any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end))
        or not 0 < end - start <= 8 * 3600
    ):
        raise BridgeError("sample qualification window must be at most eight hours")
    entries = grant["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 5:
        raise BridgeError("sample qualification accepts one to five exact entries")
    seen = set()
    for entry in entries:
        strict_keys(
            entry,
            {"operation", "payload", "source_namespace", "source_reference"},
            {"allocation_job"},
        )
        if (
            entry["operation"] not in GATES
            or entry["operation"] in seen
            or not isinstance(entry["payload"], dict)
        ):
            raise BridgeError("sample qualification permits one entry per supported operation")
        seen.add(entry["operation"])
        identifier(entry["source_namespace"])
        identifier(entry["source_reference"])
        if "allocation_job" in entry:
            if (
                entry["operation"] not in ("customer-payment.create", "supplier-payment.create")
                or not isinstance(entry["allocation_job"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", entry["allocation_job"])
            ):
                raise BridgeError("sample payment requires an exact prerequisite job")
            allocations = entry["payload"].get("allocations")
            if (
                not isinstance(allocations, list)
                or len(allocations) != 1
                or not isinstance(allocations[0], dict)
                or allocations[0].get("txn_id") != "$verified-prerequisite"
            ):
                raise BridgeError("sample payment prerequisite must bind its only allocation")


def require_window(config, policy, actor, now):
    principal = config.principals.get(actor, {})
    if "sample_qualification" not in principal:
        return None
    validate_grant(principal, actor)
    grant = principal["sample_qualification"]
    connector = config.connectors.get(grant["connector"])
    if (
        not grant["enabled"]
        or not grant["activated_at"] <= now < grant["expires_at"]
        or grant["company"] != policy.id
        or connector is None
        or connector.company != policy.id
        or connector.identity_sha256 != grant["identity_sha256"]
        or not policy.approval_required
        or policy.allow_self_approval
    ):
        raise BridgeError("sample qualification disabled, expired, or outside its bound company")
    return grant


def require_if_delegated(config, policy, store, db, job, actor, now):
    grant = require_window(config, policy, actor, now)
    if grant is None:
        return None
    entries = [e for e in grant["entries"] if e["operation"] == job["operation"]]
    if len(entries) != 1 or job["submitter"] != grant["preparer"]:
        raise BridgeError("job is outside the sample qualification authorization")
    entry = entries[0]
    expected = copy.deepcopy(entry["payload"])
    if "allocation_job" in entry:
        prerequisite = store.job(db, entry["allocation_job"])
        operation = (
            "invoice.create" if job["operation"] == "customer-payment.create" else "bill.create"
        )
        receipt = prerequisite.get("transaction_receipt", {})
        if (
            prerequisite["state"] != "verified"
            or prerequisite["operation"] != operation
            or prerequisite["submitter"] != grant["preparer"]
            or not prerequisite.get("txn_id")
            or receipt.get("identity_sha256") != grant["identity_sha256"]
            or not receipt.get("bridge_dispatched")
        ):
            raise BridgeError("sample payment prerequisite has no verified bound receipt")
        expected["allocations"][0]["txn_id"] = prerequisite["txn_id"]
    if (
        job["payload"] != expected
        or job["source"]["namespace"] != entry["source_namespace"]
        or job["source"]["reference"] != entry["source_reference"]
    ):
        raise BridgeError("payload or source differs from the exact sample authorization")
    gate_name, quota = GATES[job["operation"]]
    gate = getattr(policy, gate_name)
    if (
        gate.get("connector") != grant["connector"]
        or gate.get(quota) != 1
        or now >= gate.get("expires_at", 0)
    ):
        raise BridgeError("sample qualification requires an active one-write gate")
    evidence = job.get("master_evidence") or {}
    if evidence.get("identity_sha256") != grant["identity_sha256"]:
        raise BridgeError("sample evidence identity differs from authorization")
    if not store.verify_audit(db):
        raise BridgeError("sample qualification audit is invalid")
    for event in db.execute(
        "SELECT job_id,data FROM audit WHERE event='sample_qualification_approved'"
    ):
        prior = json.loads(event["data"])
        if (
            prior.get("grant_id") == grant["id"]
            and prior.get("operation") == job["operation"]
            and event["job_id"] != job["id"]
        ):
            raise BridgeError("sample qualification entry already bound to another job")
    return {
        "grant_id": grant["id"],
        "grant_sha256": digest(grant),
        "operation": job["operation"],
        "fingerprint": job["fingerprint"],
        "approval_kind": "operator-delegated-sample-automation",
    }
