"""Explicit review confirms extracted values without rewriting original evidence."""

import json

from .config import BridgeError
from .service import audited


def require(config, policy, store, db, job):
    uncertain = job["source"]["uncertain_fields"]
    if not uncertain:
        return
    row = db.execute(
        "SELECT actor,data FROM audit WHERE job_id=? AND event='source_reviewed' ORDER BY sequence DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    if row is None or not store.verify_audit(db):
        raise BridgeError("uncertain extracted fields require explicit source review")
    review = json.loads(row["data"])
    config.authorize(row["actor"], policy.id, "review-source")
    if review["fingerprint"] != job["fingerprint"] or review["fields"] != uncertain:
        raise BridgeError("source review differs from current evidence")


def value_at(payload, field):
    value = payload
    for part in field.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


@audited
def review(bridge, token, company, job_id, fingerprint, confirmed_values):
    config, actor, _, store = bridge._context(token, company, "review-source")
    config.authorize(actor, company, "read")
    with store.transaction() as db:
        job = store.job(db, job_id)
        uncertain = job["source"]["uncertain_fields"]
        if (
            job["state"] != "draft"
            or job["fingerprint"] != fingerprint
            or not uncertain
            or "document_id" not in job["source"]["original_values"]
        ):
            raise BridgeError("captured draft and exact review fingerprint required")
        if not isinstance(confirmed_values, dict) or set(confirmed_values) != set(uncertain):
            raise BridgeError("every uncertain field requires confirmation")
        original = job["source"]["original_values"]["extraction"]
        if any(confirmed_values[field] != value_at(original, field) for field in uncertain):
            raise BridgeError(
                "review cannot change extracted values; correction requires a new source revision"
            )
        store.event(
            db,
            bridge.clock(),
            actor,
            job_id,
            "source_reviewed",
            {"fingerprint": fingerprint, "fields": uncertain},
        )
        return {"job_id": job_id, "reviewed": True, "state": job["state"]}
