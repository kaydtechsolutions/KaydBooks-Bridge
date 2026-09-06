"""Reconcile saved invoices using durable, owned, read-only SDK evidence."""

import json
import math
import re

from qbwc_kit._xml import fromstring

from .config import BridgeError, identifier, strict_keys
from .invoice_receipt import append_lookup, lookup_context, validate_lookup
from .qbwc import DurableQBWCDiscoveryService
from .validation import canonical as canonical_payload
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
        txn_id = fromstring(request).findtext("QBXMLMsgsRq/InvoiceQueryRq/TxnID")
        times = [
            event["at"]
            for event in db.execute(
                "SELECT at,data FROM audit WHERE event='sdk_read_dispatch' ORDER BY sequence"
            )
            if json.loads(event["data"]).get("run") == evidence_id
        ]
        observed_at = times[0] if times else None
    elif reference["transport"] == "qbwc":
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
            or row["txn_id"] is None
            or session["country"] != "US"
            or session["qbxml_version"] != "17.0"
        ):
            raise BridgeError("verified QBWC receipt evidence required")
        request, response, correlation = (
            session["request_xml"],
            session["response_xml"],
            session["correlation"],
        )
        observed_at, txn_id = session["created_at"], row["txn_id"]
        if canonical_payload(payload) != row["payload"]:
            raise BridgeError("receipt evidence payload changed")
    else:
        raise BridgeError("unsupported receipt evidence transport")
    if row["actor"] != actor or row["connector"] != connector.id:
        raise BridgeError("receipt evidence ownership mismatch")
    context_hash = lookup_context(policy, payload, txn_id)
    expected = append_lookup(
        DurableQBWCDiscoveryService._discovery_request(correlation, "17.0"), correlation, txn_id
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
        "origin": "external-invoice-readback",
        "bridge_dispatched": False,
    }
