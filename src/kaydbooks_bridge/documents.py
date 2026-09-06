"""Company-scoped immutable source capture; extracted content is never authority."""

import base64
import hashlib
import math
import re

from .config import BridgeError, identifier
from .service import audited
from .validation import canonical

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MEDIA_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/csv",
    "application/json",
}


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, namespace TEXT NOT NULL,
        reference TEXT NOT NULL, media_type TEXT NOT NULL, bytes BLOB NOT NULL,
        UNIQUE(namespace,reference))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS document_no_{operation.lower()}
            BEFORE {operation} ON documents BEGIN SELECT RAISE(ABORT,'immutable source'); END""")


@audited
def capture(bridge, token, company, namespace, reference, media_type, content_base64):
    _, actor, policy, store = bridge._context(token, company, "prepare")
    identifier(namespace)
    identifier(reference)
    if (
        namespace not in policy.sources
        or not isinstance(reference, str)
        or not 1 <= len(reference) <= 128
    ):
        raise BridgeError("invalid source reference")
    if (
        media_type not in MEDIA_TYPES
        or not isinstance(content_base64, str)
        or len(content_base64) > (MAX_SOURCE_BYTES + 2) // 3 * 4
    ):
        raise BridgeError("unsupported or oversized source")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except ValueError as exc:
        raise BridgeError("invalid source encoding") from exc
    if not 1 <= len(content) <= MAX_SOURCE_BYTES:
        raise BridgeError("empty or oversized source")
    sha = hashlib.sha256(content).hexdigest()
    # The identifier also binds the source namespace/reference, not just identical bytes.
    document_id = hashlib.sha256(canonical([namespace, reference, sha]).encode()).hexdigest()
    with store.transaction() as db:
        schema(db)
        existing = db.execute(
            "SELECT * FROM documents WHERE namespace=? AND reference=?", (namespace, reference)
        ).fetchone()
        if existing and (
            existing["id"] != document_id
            or existing["owner"] != actor
            or existing["media_type"] != media_type
        ):
            raise BridgeError("source reference already captured with different content or owner")
        db.execute(
            "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?)",
            (document_id, actor, namespace, reference, media_type, content),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "source_captured",
            {"document_id": document_id, "sha256": sha, "size": len(content)},
        )
    return {
        "document_id": document_id,
        "sha256": sha,
        "size": len(content),
        "content_is_untrusted": True,
    }


def fields(value, prefix=""):
    if isinstance(value, dict):
        return {
            field
            for key, child in value.items()
            for field in fields(child, prefix + ("." if prefix else "") + key)
        }
    if isinstance(value, list):
        return {
            field
            for index, child in enumerate(value)
            for field in fields(child, prefix + f".{index}")
        }
    return {prefix}


@audited
def prepare(
    bridge, token, company, document_id, idempotency_key, payload, confidence, master_evidence=None
):
    _, actor, _, store = bridge._context(token, company, "prepare")
    if not isinstance(document_id, str) or not re.fullmatch(r"[a-f0-9]{64}", document_id):
        raise BridgeError("invalid document id")
    if (
        not isinstance(payload, dict)
        or len(canonical(payload)) > 65536
        or not isinstance(confidence, dict)
    ):
        raise BridgeError("invalid extraction")
    expected = fields(payload)
    if set(confidence) != expected or any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
        for v in confidence.values()
    ):
        raise BridgeError("explicit confidence required for every extracted field")
    with store.transaction() as db:
        schema(db)
        row = db.execute(
            "SELECT * FROM documents WHERE id=? AND owner=?", (document_id, actor)
        ).fetchone()
        if row is None:
            raise BridgeError("owned company document required")
        sha = hashlib.sha256(row["bytes"]).hexdigest()
        expected_id = hashlib.sha256(
            canonical([row["namespace"], row["reference"], sha]).encode()
        ).hexdigest()
        if expected_id != document_id or not store.verify_audit(db):
            raise BridgeError("source integrity check failed")
        source = {
            "namespace": row["namespace"],
            "reference": row["reference"],
            "sha256": sha,
            "original_values": {
                "extraction": payload,
                "confidence": confidence,
                "document_id": document_id,
                "media_type": row["media_type"],
            },
            "uncertain_fields": sorted(name for name in expected if confidence[name] < 1),
        }
    envelope = {
        "operation": "invoice.create",
        "surface": "documents",
        "idempotency_key": idempotency_key,
        "payload": payload,
        "source": source,
    }
    if master_evidence is not None:
        envelope["master_evidence"] = master_evidence
    return bridge.prepare(token, company, envelope)
