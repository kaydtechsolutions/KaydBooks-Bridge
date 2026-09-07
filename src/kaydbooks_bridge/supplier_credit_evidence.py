"""Supplier-credit evidence with original-bill limits, ownership and expiry."""

import json
import math
import re

from .config import BridgeError, identifier, strict_keys
from .qbwc import DurableQBWCDiscoveryService
from .supplier_credits import append_check, plan, validate_check
from .validation import digest


def resolve(config, policy, store, db, actor, payload, reference, now, *, txn_id=None):
    strict_keys(reference, {"transport", "connector", "id"})
    if reference["transport"] != "direct-sdk":
        raise BridgeError("credit evidence currently requires direct-sdk")
    connector = config.connectors.get(identifier(reference["connector"]))
    if connector is None or connector.company != policy.id:
        raise BridgeError("credit evidence company or connector mismatch")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    run = reference["id"]
    if not isinstance(run, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", run):
        raise BridgeError("invalid credit SDK evidence id")
    check = plan(policy, payload)
    if txn_id is not None:
        from .supplier_credits import lookup_context

        check["context_sha256"] = lookup_context(policy, payload, txn_id)
    row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (run,)).fetchone()
    expected_request = append_check(
        DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, check
    )
    if txn_id is not None:
        from .supplier_credits import append_lookup

        expected_request = append_lookup(
            DurableQBWCDiscoveryService._discovery_request(run, "17.0"),
            run,
            policy,
            payload,
            txn_id,
        )
    if (
        row is None
        or row["state"] != "verified"
        or row["error"]
        or row["actor"] != actor
        or row["connector"] != connector.id
        or row["context_hash"] != check["context_sha256"]
        or row["request"] != expected_request
        or not store.verify_audit(db)
    ):
        raise BridgeError("verified owned exact credit master evidence required")
    times = [
        event["at"]
        for event in db.execute(
            "SELECT at,data FROM audit WHERE event='sdk_read_dispatch' ORDER BY sequence"
        )
        if json.loads(event["data"]).get("run") == run
    ]
    observed = times[0] if times else None
    if (
        observed is None
        or not math.isfinite(observed)
        or not math.isfinite(now)
        or not 0 <= now - observed < policy.invoice_evidence_max_age_seconds
    ):
        raise BridgeError("credit evidence is stale; run a fresh exact check")
    if txn_id is None:
        discovery, balances = validate_check(row["response"], run, check)
        receipt = None
    else:
        from .supplier_credits import validate_lookup

        discovery, receipt = validate_lookup(row["response"], run, policy, payload, txn_id)
        balances = receipt["balances"]
    identity, _ = DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    return {
        "reference": reference,
        "balances": balances,
        **({"receipt": receipt} if receipt is not None else {}),
        "observed_at": observed,
        "context_sha256": check["context_sha256"],
        "response_sha256": digest(row["response"]),
        "identity_sha256": identity,
    }


def require(config, policy, store, db, job, now):
    saved = job.get("master_evidence")
    if (
        saved is None
        or resolve(
            config, policy, store, db, job["submitter"], job["payload"], saved["reference"], now
        )
        != saved
    ):
        raise BridgeError("fresh owned credit evidence required")
