"""Prepare a captured structured upload without model-authored field confidence."""

import hashlib
import json
import re

from . import documents
from .config import BridgeError, strict_keys
from .validation import canonical


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("duplicate JSON field in structured upload")
        result[key] = value
    return result


def _constant(_):
    raise BridgeError("non-finite JSON number in structured upload")


def prepare_upload(bridge, token, company, document_id, connector_id):
    from .hermes_qbwc import OPERATIONS
    from .web_ui import check_masters

    config, actor, _, store = bridge._context(token, company, "prepare")
    config.authorize(actor, company, "validate")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("select the company's exact connector")
    if not isinstance(document_id, str) or not re.fullmatch(r"[a-f0-9]{64}", document_id):
        raise BridgeError("invalid document id")
    with store.transaction() as db:
        documents.schema(db)
        row = db.execute(
            "SELECT * FROM documents WHERE id=? AND owner=?", (document_id, actor)
        ).fetchone()
        if row is None:
            raise BridgeError("owned company document required")
        source_hash = hashlib.sha256(row["bytes"]).hexdigest()
        expected = hashlib.sha256(
            canonical([row["namespace"], row["reference"], source_hash]).encode()
        ).hexdigest()
        if expected != document_id or not store.verify_audit(db):
            raise BridgeError("source integrity check failed")
        if row["media_type"] != "application/json" or len(row["bytes"]) > 65536:
            raise BridgeError(
                "prepare_upload requires a bounded structured JSON transaction; use document extraction or table intake for other formats"
            )
        try:
            source = json.loads(
                row["bytes"].decode("utf-8-sig"),
                object_pairs_hook=_object,
                parse_constant=_constant,
            )
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise BridgeError("invalid structured JSON upload") from exc
        strict_keys(source, {"company", "operation", "payload"})
        if source["company"] != company:
            raise BridgeError("uploaded company differs from the selected company")
        operation, payload = source["operation"], source["payload"]
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise BridgeError("entry operation is outside the selected eight")
        if not isinstance(payload, dict):
            raise BridgeError("structured transaction payload must be an object")
        existing = db.execute(
            "SELECT id FROM jobs WHERE submitter=? AND json_extract(source,'$.original_values.document_id')=?",
            (actor, document_id),
        ).fetchall()
        if existing:
            if len(existing) != 1:
                raise BridgeError(
                    "source has multiple jobs; inspect existing jobs before preparation"
                )
            job = store.job(db, existing[0]["id"])
            if (
                job["operation"] != operation
                or job["source"]["original_values"]["extraction"] != payload
            ):
                raise BridgeError(
                    "source already belongs to a different extraction; inspect existing job"
                )
            # A repeat after dispatch must report saved state without refreshing
            # evidence, resetting approval, or attempting another Add.
            if job["state"] not in ("draft", "validated"):
                return _result(job, pending=False, existing=True)
    check = check_masters(bridge, token, company, operation, connector_id, payload)
    if check["pending"]:
        if check["result"].get("state") in ("verified", "closed"):
            raise BridgeError(
                "Web Connector compatibility check finished without a match; inspect its saved result"
            )
        return {
            "pending": True,
            "stage": "waiting_for_web_connector",
            "document_id": document_id,
            "next": "repeat prepare_upload with the same document_id and connector_id after Web Connector updates",
            "posting_performed": False,
        }
    # Confidence describes exact parsing of the captured bytes, not the correctness
    # of source accounting or approval. Unsupported/uncertain documents use extraction.
    confidence = {name: 1 for name in documents.fields(payload)}
    job = documents.prepare(
        bridge,
        token,
        company,
        document_id,
        job["idempotency_key"] if existing else "upload-" + document_id[:48],
        payload,
        confidence,
        check["evidence"],
        operation=operation,
    )
    if job["state"] == "draft":
        job = bridge.action(token, company, job["id"], "validate")
    result = _result(job, pending=False, existing=bool(existing))
    if job["state"] == "validated":
        result["review"] = bridge.preview(token, company, job["id"])
    return result


def _result(job, *, pending, existing):
    return {
        "pending": pending,
        "existing": existing,
        "job_id": job["id"],
        "state": job["state"],
        "txn_id": job.get("txn_id"),
        "payload": job["payload"],
        "ready_for_review": job["state"] == "validated",
        "posting_performed": False,
        "next": "already verified; report saved result without preparing or posting again"
        if job["state"] == "verified"
        else "exact human confirmation is required before posting; use status for unresolved jobs",
    }
