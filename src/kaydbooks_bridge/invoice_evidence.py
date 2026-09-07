"""Resolve invoice evidence from durable company state, never from client claims."""

import json
import math
import re

from .config import BridgeError, identifier, strict_keys
from .invoice_compatibility import plan, validate_response
from .qbwc import DurableQBWCDiscoveryService
from .validation import digest


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    connector = config.connectors.get(identifier(reference["connector"]))
    if connector is None or connector.company != policy.id:
        raise BridgeError("invoice evidence company or connector mismatch")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    if not store.verify_audit(db):
        raise BridgeError("invoice evidence audit is invalid")
    check = plan(policy, payload)
    transport, evidence_id = reference["transport"], reference["id"]
    if transport == "direct-sdk":
        if not isinstance(evidence_id, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", evidence_id):
            raise BridgeError("invalid SDK evidence id")
        row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (evidence_id,)).fetchone()
        if row is None or row["state"] != "verified" or row["error"]:
            raise BridgeError("verified SDK invoice evidence required")
        # Use the FIRST dispatch, including across recovery. A replay or a delayed
        # helper response can never make an old observation look newly collected.
        times = [
            event["at"]
            for event in db.execute(
                "SELECT at,data FROM audit WHERE event='sdk_read_dispatch' ORDER BY sequence"
            )
            if json.loads(event["data"]).get("run") == evidence_id
        ]
        observed_at = times[0] if times else None
        response = row["response"]
        context = {"correlation": evidence_id, "country": "US", "qbxml_version": "17.0"}
    elif transport == "qbwc":
        identifier(evidence_id)
        row = db.execute("SELECT * FROM qbwc_invoice_jobs WHERE id=?", (evidence_id,)).fetchone()
        session = (
            None
            if row is None
            else db.execute(
                "SELECT * FROM qbwc_sessions WHERE ticket=?", (row["ticket"],)
            ).fetchone()
        )
        if (
            session is None
            or session["state"] not in ("verified", "closed")
            or session["response_result"] != 100
            or session["last_error"]
            or session["connector"] != connector.id
        ):
            raise BridgeError("verified QBWC invoice evidence required")
        observed_at, response, context = session["created_at"], session["response_xml"], session
    else:
        raise BridgeError("unsupported invoice evidence transport")
    if row["actor"] != actor or row["connector"] != connector.id:
        raise BridgeError("invoice evidence ownership mismatch")
    if row["context_hash"] != check["context_sha256"]:
        raise BridgeError("invoice evidence payload or policy changed")
    if (
        observed_at is None
        or not math.isfinite(observed_at)
        or not math.isfinite(now)
        or not 0 <= now - observed_at < policy.invoice_evidence_max_age_seconds
    ):
        raise BridgeError("invoice evidence is stale or has no trusted timestamp; run a new check")
    discovery = validate_response(response, context["correlation"], check)
    identity, _ = DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, context, connector
    )
    return {
        "reference": reference,
        "observed_at": observed_at,
        "context_sha256": check["context_sha256"],
        "response_sha256": digest(response),
        "identity_sha256": identity,
    }


def require(config, policy, store, db, job, now):
    saved = job.get("master_evidence")
    if saved is None:
        if policy.invoice_masters:
            raise BridgeError("verified invoice master evidence required")
        return
    current = resolve(
        config, policy, store, db, job["submitter"], job["payload"], saved["reference"], now
    )
    if current != saved:
        raise BridgeError("linked invoice evidence changed")
