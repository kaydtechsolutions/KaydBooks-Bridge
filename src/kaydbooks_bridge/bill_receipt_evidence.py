"""Reconcile saved bills using durable, owned, read-only SDK evidence."""

import json
import math
import re

from qbwc_kit._xml import fromstring

from .bill_receipt import append_lookup, lookup_context, validate_lookup
from .config import BridgeError, identifier, strict_keys
from .qbwc import DurableQBWCDiscoveryService
from .validation import digest


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    connector = config.connectors.get(identifier(reference["connector"]))
    if connector is None or connector.company != policy.id:
        raise BridgeError("receipt company or connector mismatch")
    evidence_id = reference["id"]
    if not store.verify_audit(db):
        raise BridgeError("receipt evidence audit is invalid")
    if reference["transport"] == "direct-sdk":
        if not isinstance(evidence_id, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", evidence_id):
            raise BridgeError("invalid receipt evidence id")
        row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (evidence_id,)).fetchone()
        if row is None or row["state"] != "verified" or row["error"]:
            raise BridgeError("verified SDK receipt evidence required")
        request, response, correlation = row["request"], row["response"], evidence_id
        txn_id = fromstring(request).findtext("QBXMLMsgsRq/BillQueryRq/TxnID")
        times = [
            event["at"]
            for event in db.execute(
                "SELECT at,data FROM audit WHERE event='sdk_read_dispatch' ORDER BY sequence"
            )
            if json.loads(event["data"]).get("run") == evidence_id
        ]
        observed_at = times[0] if times else None
    else:
        raise BridgeError("unsupported receipt evidence transport")
    if row["actor"] != actor or row["connector"] != connector.id:
        raise BridgeError("receipt evidence ownership mismatch")
    context_hash = lookup_context(policy, payload, txn_id)
    expected = append_lookup(
        DurableQBWCDiscoveryService._discovery_request(correlation, "17.0"),
        correlation,
        txn_id,
        policy,
        payload,
    )
    if row["context_hash"] != context_hash or request != expected:
        raise BridgeError("receipt evidence payload, policy or request changed")
    if (
        observed_at is None
        or not math.isfinite(observed_at)
        or not math.isfinite(now)
        or not 0 <= now - observed_at < policy.invoice_evidence_max_age_seconds
    ):
        raise BridgeError("receipt evidence is stale; run a new read-only check")
    discovery, receipt = validate_lookup(response, correlation, policy, payload, txn_id)
    identity, _ = DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": correlation, "country": "US", "qbxml_version": "17.0"}, connector
    )
    return {
        "reference": reference,
        "observed_at": observed_at,
        "context_sha256": context_hash,
        "response_sha256": digest(response),
        "identity_sha256": identity,
        "receipt": receipt,
        "origin": "external-bill-readback",
        "bridge_dispatched": False,
    }
