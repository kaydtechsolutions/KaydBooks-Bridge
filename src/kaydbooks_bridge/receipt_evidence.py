"""Reconcile saved invoices using durable, owned, read-only SDK evidence."""

import json
import math
import re

from qbwc_kit._xml import fromstring

from .config import BridgeError, identifier, strict_keys
from .invoice_receipt import append_lookup, lookup_context, validate_lookup
from .qbwc import DurableQBWCDiscoveryService
from .validation import digest


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    if reference["transport"] != "direct-sdk":
        raise BridgeError("receipt attachment requires direct SDK evidence")
    connector = config.connectors.get(identifier(reference["connector"]))
    if connector is None or connector.company != policy.id:
        raise BridgeError("receipt company or connector mismatch")
    evidence_id = reference["id"]
    if not isinstance(evidence_id, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", evidence_id):
        raise BridgeError("invalid receipt evidence id")
    if not store.verify_audit(db):
        raise BridgeError("receipt evidence audit is invalid")
    row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (evidence_id,)).fetchone()
    if row is None or row["state"] != "verified" or row["error"]:
        raise BridgeError("verified SDK receipt evidence required")
    if row["actor"] != actor or row["connector"] != connector.id:
        raise BridgeError("receipt evidence ownership mismatch")
    txn_id = fromstring(row["request"]).findtext("QBXMLMsgsRq/InvoiceQueryRq/TxnID")
    context_hash = lookup_context(policy, payload, txn_id)
    expected = append_lookup(
        DurableQBWCDiscoveryService._discovery_request(evidence_id, "17.0"), evidence_id, txn_id
    )
    if row["context_hash"] != context_hash or row["request"] != expected:
        raise BridgeError("receipt evidence payload, policy or request changed")
    times = [
        event["at"]
        for event in db.execute(
            "SELECT at,data FROM audit WHERE event='sdk_read_dispatch' ORDER BY sequence"
        )
        if json.loads(event["data"]).get("run") == evidence_id
    ]
    if (
        not times
        or not math.isfinite(times[0])
        or not math.isfinite(now)
        or not 0 <= now - times[0] < policy.invoice_evidence_max_age_seconds
    ):
        raise BridgeError("receipt evidence is stale; run a new read-only check")
    discovery, receipt = validate_lookup(row["response"], evidence_id, policy, payload, txn_id)
    identity, _ = DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": evidence_id, "country": "US", "qbxml_version": "17.0"}, connector
    )
    return {
        "reference": reference,
        "observed_at": times[0],
        "context_sha256": context_hash,
        "response_sha256": digest(row["response"]),
        "identity_sha256": identity,
        "receipt": receipt,
        "origin": "external-invoice-readback",
        "bridge_dispatched": False,
    }
