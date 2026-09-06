"""Bill-specific verified SDK master evidence; invoice evidence is not interchangeable."""

import json
import math
import re

from .bill_lookup import append_check, plan, validate_check
from .config import BridgeError, identifier, strict_keys
from .qbwc import DurableQBWCDiscoveryService
from .validation import digest


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    if reference["transport"] != "direct-sdk":
        raise BridgeError("bill evidence currently requires direct-sdk")
    connector = config.connectors.get(identifier(reference["connector"]))
    if connector is None or connector.company != policy.id:
        raise BridgeError("bill evidence company or connector mismatch")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    run = reference["id"]
    if not isinstance(run, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", run):
        raise BridgeError("invalid bill SDK evidence id")
    check = plan(policy, payload)
    row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (run,)).fetchone()
    expected_request = append_check(
        DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, check
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
        raise BridgeError("verified owned exact bill master evidence required")
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
        raise BridgeError("bill evidence is stale; run a fresh exact check")
    discovery = validate_check(row["response"], run, check)
    identity, _ = DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    return {
        "reference": reference,
        "observed_at": observed,
        "context_sha256": check["context_sha256"],
        "response_sha256": digest(row["response"]),
        "identity_sha256": identity,
    }
