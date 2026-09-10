"""Immutable reviewed batches; channel confirmation is separate from agent tools."""

import json
import secrets

from .config import BridgeError, company_policy_context
from .hermes_qbwc import OPERATIONS
from .qbwc_posting import enqueue
from .service import audited
from .validation import canonical, digest

MAX_BATCH_ENTRIES = 30
MAX_BATCH_MANIFEST_BYTES = 60000


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS hermes_batches (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, manifest TEXT NOT NULL,
        manifest_hash TEXT NOT NULL, confirmation_code TEXT NOT NULL,
        created_at REAL NOT NULL, expires_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS hermes_batch_jobs (
        batch_id TEXT REFERENCES hermes_batches(id), job_id TEXT REFERENCES jobs(id),
        PRIMARY KEY(batch_id,job_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS hermes_batch_confirmations (
        batch_id TEXT PRIMARY KEY REFERENCES hermes_batches(id), reviewer TEXT NOT NULL,
        channel_event_id TEXT NOT NULL UNIQUE, sender TEXT NOT NULL, confirmed_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS hermes_batch_delivery (
        batch_id TEXT REFERENCES hermes_batches(id), kind TEXT NOT NULL,
        text TEXT NOT NULL, text_hash TEXT NOT NULL, claimed_at REAL NOT NULL,
        PRIMARY KEY(batch_id,kind))""")
    db.execute("""CREATE TABLE IF NOT EXISTS hermes_batch_delivery_receipts (
        batch_id TEXT NOT NULL, kind TEXT NOT NULL, provider_id TEXT NOT NULL,
        recorded_at REAL NOT NULL, PRIMARY KEY(batch_id,kind),
        FOREIGN KEY(batch_id,kind) REFERENCES hermes_batch_delivery(batch_id,kind))""")
    for table in (
        "hermes_batches",
        "hermes_batch_jobs",
        "hermes_batch_confirmations",
        "hermes_batch_delivery",
        "hermes_batch_delivery_receipts",
    ):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_guard
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable Hermes evidence'); END""")


def owned(store, db, actor, batch_id):
    schema(db)
    row = db.execute(
        "SELECT * FROM hermes_batches WHERE id=? AND owner=?", (batch_id, actor)
    ).fetchone()
    if (
        row is None
        or not store.verify_audit(db)
        or digest(json.loads(row["manifest"])) != row["manifest_hash"]
    ):
        raise BridgeError("owned intact batch required")
    return row, json.loads(row["manifest"])


@audited
def create(bridge, token, company, job_ids):
    config, actor, policy, store = bridge._context(token, company, "prepare")
    config.authorize(actor, company, "validate")
    if (
        not isinstance(job_ids, list)
        or not 1 <= len(job_ids) <= MAX_BATCH_ENTRIES
        or any(not isinstance(j, str) for j in job_ids)
        or len(set(job_ids)) != len(job_ids)
    ):
        raise BridgeError(f"one to {MAX_BATCH_ENTRIES} distinct job IDs required")
    rows = []
    for j in job_ids:
        job = bridge.status(token, company, j)
        if job["operation"] not in OPERATIONS or not job["source"]["original_values"].get(
            "document_id"
        ):
            raise BridgeError("source-bound selected entry required")
        preview = bridge.preview(token, company, j)
        rows.append(
            {
                "job_id": j,
                "fingerprint": job["fingerprint"],
                "source_sha256": job["source"]["sha256"],
                "operation": job["operation"],
                "payload": job["payload"],
                "preview_hash": preview["preview_sha256"],
            }
        )
    if not policy.approval_required or policy.allow_self_approval:
        raise BridgeError("independent company approval must be enabled")
    manifest = {
        "company": company,
        "policy_hash": digest(company_policy_context(policy)),
        "rows": rows,
    }
    value_hash = digest(manifest)
    batch_id = digest([actor, value_hash])[:24]
    # Bound previews fit a small number of WhatsApp text chunks; do not silently truncate rows.
    if len(canonical(manifest)) > MAX_BATCH_MANIFEST_BYTES:
        raise BridgeError("batch preview too long; prepare smaller batches")
    with store.transaction() as db:
        schema(db)
        now = bridge.clock()
        previous = db.execute("SELECT * FROM hermes_batches WHERE id=?", (batch_id,)).fetchone()
        while previous and now >= previous["expires_at"]:
            batch_id = digest([batch_id, previous["expires_at"]])[:24]
            previous = db.execute("SELECT * FROM hermes_batches WHERE id=?", (batch_id,)).fetchone()
        if not previous:
            if db.execute(
                "SELECT 1 FROM hermes_batch_jobs j JOIN hermes_batches b ON b.id=j.batch_id WHERE b.expires_at>? AND j.job_id IN ("
                + ",".join("?" for _ in job_ids)
                + ")",
                [now, *job_ids],
            ).fetchone():
                raise BridgeError("job already belongs to an immutable batch")
            now = bridge.clock()
            db.execute(
                "INSERT INTO hermes_batches VALUES(?,?,?,?,?,?,?)",
                (
                    batch_id,
                    actor,
                    canonical(manifest),
                    value_hash,
                    secrets.token_hex(8),
                    now,
                    now + 900,
                ),
            )
            db.executemany(
                "INSERT INTO hermes_batch_jobs VALUES(?,?)", [(batch_id, j) for j in job_ids]
            )
            store.event(
                db,
                now,
                actor,
                None,
                "hermes_batch_prepared",
                {"batch_id": batch_id, "manifest_hash": value_hash, "jobs": job_ids},
            )
    return status(bridge, token, company, batch_id)


def preview_text(row, manifest):
    parts = [
        f"KB v0.1.0 | {manifest['company']} | Batch {row['id']}",
        "Review every entry below. Confirmation authorizes these exact entries in the configured company.",
    ]
    for index, entry in enumerate(manifest["rows"], 1):
        parts.append(
            f"{index}. {entry['operation']}\n"
            + json.dumps(entry["payload"], ensure_ascii=False, indent=2)
        )
    parts.append(f"To confirm and post, reply:\n/kb-confirm {row['id']} {row['confirmation_code']}")
    return "\n\n".join(parts)


def preview_observed(db, batch_id, manifest_hash):
    return any(
        (data := json.loads(row["data"])).get("batch_id") == batch_id
        and data.get("manifest_hash") == manifest_hash
        for row in db.execute("SELECT data FROM audit WHERE event='hermes_preview_observed'")
    )


@audited
def observe_preview(bridge, token, company, batch_id, evidence_reference):
    """Administrator records an explicit human receipt observation; not an MCP tool.

    This never fabricates a provider acknowledgment or confirms accounting entries.
    The operator must still give their exact native-channel confirmation.
    """
    _, actor, _, store = bridge._context(token, company, "review-source")
    if not isinstance(evidence_reference, str) or not 16 <= len(evidence_reference) <= 500:
        raise BridgeError("retained human receipt evidence required")
    with store.transaction() as db:
        row, _ = owned(store, db, actor, batch_id)
        if not db.execute(
            "SELECT 1 FROM hermes_batch_delivery WHERE batch_id=? AND kind='preview'", (batch_id,)
        ).fetchone():
            raise BridgeError("claimed preview required")
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "hermes_preview_observed",
            {
                "batch_id": batch_id,
                "manifest_hash": row["manifest_hash"],
                "evidence_reference": evidence_reference,
                "provider_acknowledgment": False,
            },
        )
    return {"human_observed": True, "provider_acknowledgment": False}


@audited
def status(bridge, token, company, batch_id):
    _, actor, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        row, manifest = owned(store, db, actor, batch_id)
        confirmed = bool(
            db.execute(
                "SELECT 1 FROM hermes_batch_confirmations WHERE batch_id=?", (batch_id,)
            ).fetchone()
        )
        jobs = [store.job(db, r["job_id"]) for r in manifest["rows"]]
        for source, job in zip(manifest["rows"], jobs, strict=True):
            if job["fingerprint"] != source["fingerprint"]:
                raise BridgeError("batch job changed")
        states = {
            name: sum(j["state"] == name for j in jobs)
            for name in sorted({j["state"] for j in jobs})
        }
        complete = all(j["state"] == "verified" for j in jobs)

        def is_held(job):
            if job["state"] in ("blocked", "failed"):
                return True
            if job["state"] not in ("unknown", "posted-unverified"):
                return False
            # A successful Add is followed by readback in the same QBWC session.
            # Do not send a premature failure result while that read is active.
            return not db.execute(
                "SELECT 1 FROM qbwc_invoice_runs WHERE job_id=? AND phase NOT IN ('done','held')",
                (job["id"],),
            ).fetchone()

        expired = bridge.clock() >= row["expires_at"]
        held = any(is_held(j) for j in jobs) or (
            confirmed and expired and any(j["state"] in ("validated", "queued") for j in jobs)
        )
        deliveries = {
            r["kind"]: bool(r["provider_id"])
            for r in db.execute(
                "SELECT d.kind,p.provider_id FROM hermes_batch_delivery d LEFT JOIN hermes_batch_delivery_receipts p ON p.batch_id=d.batch_id AND p.kind=d.kind WHERE d.batch_id=?",
                (batch_id,),
            )
        }
        details = [
            {
                "job_id": j["id"],
                "ref_number": j["payload"].get("ref_number"),
                "operation": j["operation"],
                "state": j["state"],
                "txn_id": j["txn_id"],
            }
            for j in jobs
        ]
        return {
            "batch_id": batch_id,
            "manifest_hash": row["manifest_hash"],
            "confirmed": confirmed,
            "expired": expired,
            "complete": complete,
            "held": held,
            "states": states,
            "entries": details,
            "deliveries": deliveries,
            "preview_observed": preview_observed(db, batch_id, row["manifest_hash"]),
            "preview": preview_text(row, manifest),
        }


def confirm(bridge, token, company, batch_id, code, *, reviewer_token, event_id, sender):
    """Trusted channel adapter only; never registered as an agent/MCP tool."""
    config, actor, policy, store = bridge._context(token, company, "read")
    reviewer = config.authenticate(reviewer_token)
    config.authorize(reviewer, company, "approve")
    if (
        reviewer == actor
        or not isinstance(event_id, str)
        or not 1 <= len(event_id) <= 200
        or not sender
    ):
        raise BridgeError("separate reviewer and actual channel event required")
    with store.transaction() as db:
        row, manifest = owned(store, db, actor, batch_id)
        if not secrets.compare_digest(str(code), row["confirmation_code"]):
            raise BridgeError("exact confirmation code required")
        previous = db.execute(
            "SELECT * FROM hermes_batch_confirmations WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if previous:
            if previous["reviewer"] != reviewer or previous["sender"] != sender:
                raise BridgeError("batch confirmation belongs to another reviewer")
            return {"batch_id": batch_id, "already_confirmed": True}
        if db.execute(
            "SELECT 1 FROM hermes_batch_confirmations WHERE channel_event_id=?", (event_id,)
        ).fetchone():
            raise BridgeError("channel event already used")
        if bridge.clock() >= row["expires_at"] or manifest["policy_hash"] != digest(
            company_policy_context(policy)
        ):
            raise BridgeError(
                "batch review expired or company policy changed; prepare a new review"
            )
        if not db.execute(
            "SELECT 1 FROM hermes_batch_delivery_receipts WHERE batch_id=? AND kind='preview'",
            (batch_id,),
        ).fetchone() and not preview_observed(db, batch_id, row["manifest_hash"]):
            raise BridgeError("exact preview must be sent before confirmation")
        for entry in manifest["rows"]:
            job = store.job(db, entry["job_id"])
            if job["state"] != "validated" or job["fingerprint"] != entry["fingerprint"]:
                raise BridgeError("batch job no longer matches the review")
        db.execute(
            "INSERT INTO hermes_batch_confirmations VALUES(?,?,?,?,?)",
            (batch_id, reviewer, event_id, sender, bridge.clock()),
        )
        store.event(
            db,
            bridge.clock(),
            reviewer,
            None,
            "hermes_batch_human_confirmed",
            {"batch_id": batch_id, "event_id": event_id, "manifest_hash": row["manifest_hash"]},
        )
    return {"batch_id": batch_id, "confirmed": True}


def advance(bridge, token, company, batch_id, *, reviewer_token):
    """One queued write at a time; uncertain entries stop this batch."""
    config, actor, policy, store = bridge._context(token, company, "post-sample")
    with store.transaction() as db:
        row, manifest = owned(store, db, actor, batch_id)
        approval = db.execute(
            "SELECT * FROM hermes_batch_confirmations WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if not approval or approval["reviewer"] != config.authenticate(reviewer_token):
            raise BridgeError("trusted batch confirmation required")
        if (
            manifest["policy_hash"] != digest(company_policy_context(policy))
            or bridge.clock() >= row["expires_at"]
        ):
            raise BridgeError("batch dispatch window or policy changed")
    progress = status(bridge, token, company, batch_id)
    if progress["complete"] or progress["held"]:
        return progress
    for entry in progress["entries"]:
        if entry["state"] == "verified":
            continue
        if entry["state"] in ("in-flight", "posted-unverified", "unknown"):
            return progress
        j = entry["job_id"]
        if entry["state"] == "validated":
            bridge.action(reviewer_token, company, j, "approve")
            bridge.action(token, company, j, "submit")
        # Pause is still an operator control: the worker cannot clear it.
        enqueue(bridge, token, company, j)
        break
    return status(bridge, token, company, batch_id)


def claim_delivery(bridge, token, company, batch_id, kind):
    _, actor, _, store = bridge._context(token, company, "read")
    progress = status(bridge, token, company, batch_id)
    if (
        kind not in ("preview", "result")
        or (kind == "preview" and progress["expired"])
        or (kind == "result" and not (progress["complete"] or progress["held"]))
    ):
        raise BridgeError("delivery not ready")
    text = (
        progress["preview"]
        if kind == "preview"
        else "\n".join(
            [
                f"KB v0.1.0 | {company} | Batch {batch_id}",
                " | ".join(f"{k}: {v}" for k, v in progress["states"].items()),
                *[
                    f"{e['ref_number']} | {e['operation']} | {e['state']}"
                    for e in progress["entries"]
                ],
                "Unfinished entries held. Reconcile uncertain writes before continuing; never resubmit them."
                if progress["held"]
                else "All entries verified by QuickBooks readback.",
            ]
        )
    )
    with store.transaction() as db:
        if db.execute(
            "SELECT 1 FROM hermes_batch_delivery WHERE batch_id=? AND kind=?", (batch_id, kind)
        ).fetchone():
            return {"claimed": False}
        db.execute(
            "INSERT INTO hermes_batch_delivery VALUES(?,?,?,?,?)",
            (batch_id, kind, text, digest(text), bridge.clock()),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "hermes_delivery_claimed",
            {"batch_id": batch_id, "kind": kind, "text_hash": digest(text)},
        )
    return {"claimed": True, "text": text, "text_hash": digest(text)}


def acknowledge_delivery(bridge, token, company, batch_id, kind, provider_id):
    _, actor, _, store = bridge._context(token, company, "read")
    if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 1024:
        raise BridgeError("provider acknowledgment required")
    with store.transaction() as db:
        owned(store, db, actor, batch_id)
        old = db.execute(
            "SELECT provider_id FROM hermes_batch_delivery_receipts WHERE batch_id=? AND kind=?",
            (batch_id, kind),
        ).fetchone()
        if old and old[0] != provider_id:
            raise BridgeError("delivery acknowledgment differs")
        if not db.execute(
            "SELECT 1 FROM hermes_batch_delivery WHERE batch_id=? AND kind=?", (batch_id, kind)
        ).fetchone():
            raise BridgeError("delivery must be claimed first")
        db.execute(
            "INSERT OR IGNORE INTO hermes_batch_delivery_receipts VALUES(?,?,?,?)",
            (batch_id, kind, provider_id, bridge.clock()),
        )
        if not old:
            store.event(
                db,
                bridge.clock(),
                actor,
                None,
                "hermes_delivery_acknowledged",
                {"batch_id": batch_id, "kind": kind, "provider_id": provider_id},
            )
    return {"acknowledged": True}
