"""One-shot controlled sample master changes and independent saved-record recovery."""

import hashlib
import json
import math
import re
import time
import uuid

from . import master_checks as checks
from . import master_records as records
from .config import BridgeError, Config, identifier, strict_keys
from .direct_sdk import company_lock
from .service import Bridge, audited
from .source_review import require as require_review
from .validation import digest, validate_source


def validate_gate(value):
    if not isinstance(value, dict):
        raise BridgeError("sample master gate must be an object")
    if not value:
        return
    strict_keys(
        value, {"connector", "authorization", "name_prefix", "max_writes", "expires_at", "kinds"}
    )
    identifier(value["connector"])
    if not isinstance(value["authorization"], str) or not 20 <= len(value["authorization"]) <= 1000:
        raise BridgeError("explicit sample master authorization required")
    if (
        not isinstance(value["name_prefix"], str)
        or not re.fullmatch(r"[A-Za-z0-9 -]{3,16}", value["name_prefix"])
        or not value["name_prefix"].strip()
    ):
        raise BridgeError("bounded sample master name prefix required")
    if (
        type(value["max_writes"]) is not int
        or not 1 <= value["max_writes"] <= 20
        or type(value["expires_at"]) not in (int, float)
        or not math.isfinite(value["expires_at"])
    ):
        raise BridgeError("bounded sample master quota and expiration required")
    kinds = value["kinds"]
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(not isinstance(k, str) or k not in records.KINDS for k in kinds)
        or len(kinds) != len(set(kinds))
    ):
        raise BridgeError("explicit sample master kinds required")


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS native_master_attempts (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
        connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
        request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
    for action in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_master_no_{action.lower()}
            BEFORE {action} ON native_master_attempts BEGIN SELECT RAISE(ABORT,'immutable master attempt'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS native_master_insert_guard BEFORE INSERT ON native_master_attempts
        WHEN NOT EXISTS (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='master.change'
        AND state='queued' AND submitter=NEW.actor AND attempt IS NULL)
        BEGIN SELECT RAISE(ABORT,'owned queued master proposal required'); END""")


def specification(job):
    return {
        "kind": job["payload"]["kind"],
        "list_id": job["payload"].get("target", {}).get("list_id"),
        "payload": job["payload"],
    }


def context(policy, connector, job):
    return checks.context(policy, connector, specification(job))


def gate(config, actor, policy, store, db, job, now):
    if job["operation"] != "master.change" or job["submitter"] != actor:
        raise BridgeError("owned master proposal required")
    for permission in ("post-sample", "read", "validate", "recover", "submit"):
        config.authorize(actor, policy.id, permission)
    settings = policy.sample_master_posting
    validate_gate(settings)
    payload = job["payload"]
    if not settings or now >= settings["expires_at"] or payload["kind"] not in settings["kinds"]:
        raise BridgeError("sample master authorization absent or expired")
    connector = config.connectors.get(settings["connector"])
    if connector is None or connector.company != policy.id or connector.identity_sha256 == "0" * 64:
        raise BridgeError("confirmed sample master connector required")
    original = job["master_evidence"]["original"]
    names = [payload["fields"]["name"]] if "name" in payload["fields"] else []
    if original:
        names.append(original["Name"])
    if not names or any(not n.startswith(settings["name_prefix"]) for n in names):
        raise BridgeError("master name outside sample scope")
    if payload["action"] == "update":
        # Only records created and independently verified by this Bridge can be
        # changed under this sample gate. No pre-existing business records qualify.
        created = False
        for row in db.execute("SELECT data FROM audit WHERE event='native_master_verified'"):
            proof = json.loads(row["data"])
            if (
                proof.get("action") == "create"
                and proof.get("kind") == payload["kind"]
                and proof.get("list_id") == payload["target"]["list_id"]
            ):
                created = True
                break
        if not created:
            raise BridgeError("sample update requires a Bridge-created verified test master")
    validate_source(job["source"], policy)
    Bridge._approval(config, policy, job)
    require_review(config, policy, store, db, job)
    checks.require(config, policy, store, db, job, now)
    if not store.verify_audit(db):
        raise BridgeError("invalid master audit")
    return connector


def exchange(request, write, folder, approve):
    from .sample_posting import windows_exchange

    return windows_exchange(request, write, folder, approve, helper="native_master.ps1")


@audited
def post(bridge, token, company, job_id, *, transport=exchange, read_transport=checks.exchange):
    config, actor, policy, store = bridge._context(token, company, "post-sample")
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            schema(db)
            job = store.job(db, job_id)
            connector = gate(config, actor, policy, store, db, job, bridge.clock())
            if job["state"] != "queued":
                raise BridgeError("queued master required; never retry dispatched work")
            if db.execute("SELECT paused FROM control").fetchone()[0]:
                raise BridgeError("company paused")
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('in-flight','unknown','posted-unverified')"
            ).fetchone():
                raise BridgeError("unresolved company write")
            if (
                db.execute(
                    "SELECT 1 FROM qbwc_sessions WHERE state IN ('authenticated','request-sent','verified','blocked')"
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
                ).fetchone()
                or db.execute("SELECT 1 FROM master_checks WHERE state='dispatched'").fetchone()
            ):
                raise BridgeError("company read session active")
            if (
                db.execute("SELECT COUNT(*) FROM native_master_attempts").fetchone()[0]
                >= policy.sample_master_posting["max_writes"]
            ):
                raise BridgeError("sample master quota reached")
            attempt = uuid.uuid4().hex
            run = str(time.time_ns())[-12:]
            write = records.request(job["payload"], policy, run + "998", external_guid=job_id)
            fingerprint = context(policy, connector, job)
            db.execute(
                "INSERT INTO native_master_attempts VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    attempt,
                    connector.id,
                    actor,
                    bridge.clock(),
                    write,
                    fingerprint,
                    policy.sample_master_posting["authorization"],
                ),
            )
            db.execute(
                "UPDATE jobs SET state='in-flight',attempt=?,lease_until=? WHERE id=?",
                (attempt, bridge.clock() + 360, job_id),
            )
            store.event(
                db,
                bridge.clock(),
                actor,
                job_id,
                "sample_master_dispatch_prepared",
                {"attempt": attempt, "request_hash": digest(write)},
            )

        def approve(text):
            current, current_actor, current_policy, _ = bridge._context(
                token, company, "post-sample"
            )
            with store.transaction() as db:
                active = store.job(db, job_id)
                current_connector = gate(
                    current, current_actor, current_policy, store, db, active, bridge.clock()
                )
                if (
                    active["state"] != "in-flight"
                    or active["attempt"] != attempt
                    or active["lease_until"] <= bridge.clock()
                    or db.execute("SELECT paused FROM control").fetchone()[0]
                    or current_connector != connector
                    or context(current_policy, connector, active) != fingerprint
                ):
                    raise BridgeError("master dispatch authority changed")
                checks.response(text, current_policy, connector, specification(active), run)
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    job_id,
                    "sample_master_write_authorized",
                    {"preflight_hash": digest(text)},
                )
            return True

        folder = store.path.parent / ("native-master-" + attempt)
        try:
            answer = transport(
                checks.request(policy, specification(job), run), write, folder, approve
            )
            if answer is None:
                raise BridgeError("master response missing; reconcile without resend")
            saved = records.response(
                answer,
                job["payload"]["kind"],
                "Add" if job["payload"]["action"] == "create" else "Mod",
                run + "998",
            )
            verified = records.compare(
                job["payload"], policy, saved, job["master_evidence"]["original"]
            )
            if job["payload"]["action"] == "create" and saved.get("ExternalGUID", "").strip(
                "{}"
            ).lower() != str(uuid.UUID(job_id)):
                raise BridgeError("master creation identity differs")
            bridge._finish(
                store,
                actor,
                job_id,
                attempt,
                "posted-unverified",
                "native_master_receipt_saved",
                verified["list_id"],
            )
        except BaseException:
            with store.transaction() as db:
                if store.job(db, job_id)["state"] == "in-flight":
                    db.execute(
                        "UPDATE jobs SET state='unknown',detail='native_master_outcome_requires_reconciliation' WHERE id=?",
                        (job_id,),
                    )
                    store.event(
                        db,
                        bridge.clock(),
                        actor,
                        job_id,
                        "native_master_outcome_unknown",
                        {"attempt": attempt},
                    )
            raise
    return reconcile(bridge, token, company, job_id, transport=read_transport)


@audited
def reconcile(bridge, token, company, job_id, *, transport=checks.exchange):
    config, actor, policy, store = bridge._context(token, company, "recover")
    config.authorize(actor, company, "read")
    config.authorize(actor, company, "validate")
    with store.transaction() as db:
        schema(db)
        job = store.job(db, job_id)
        attempt = db.execute(
            "SELECT * FROM native_master_attempts WHERE job_id=?", (job_id,)
        ).fetchone()
        if (
            attempt is None
            or attempt["actor"] != actor
            or job["state"] not in ("posted-unverified", "unknown")
            or not store.verify_audit(db)
        ):
            raise BridgeError("owned uncertain master attempt required")
        connector = config.connectors[attempt["connector"]]
        if (
            connector.company != company
            or context(policy, connector, job) != attempt["context_hash"]
        ):
            raise BridgeError("original master dispatch context required")
    folder = store.path.parent / ("native-master-" + attempt["attempt"])
    if (
        not (folder / "closed.txt").exists()
        or not (folder / "write-intent.txt").exists()
        or (folder / "write-intent.txt").read_text(encoding="utf-8")
        != hashlib.sha256(attempt["request"].encode()).hexdigest()
    ):
        raise BridgeError(
            "native master helper must close with retained write intent; never resend"
        )
    payload = job["payload"]
    selection = {"list_id": job["txn_id"] or payload.get("target", {}).get("list_id")}
    if selection["list_id"] is None:
        selection = {"full_name": payload["fields"]["name"]}
    observed = checks.read(
        bridge, token, company, connector.id, payload["kind"], transport=transport, **selection
    )
    saved = observed["record"]
    if payload["action"] == "create" and saved.get("ExternalGUID", "").strip("{}").lower() != str(
        uuid.UUID(job_id)
    ):
        raise BridgeError("saved master does not carry this creation identity")
    proof = records.compare(payload, policy, saved, job["master_evidence"]["original"])
    latest = Config.load(bridge.config_path)
    latest_actor = latest.authenticate(token)
    latest_policy = latest.authorize(latest_actor, company, "recover")
    if (
        latest_actor != actor
        or latest.connectors.get(connector.id) != connector
        or context(latest_policy, connector, job) != attempt["context_hash"]
    ):
        raise BridgeError("master context changed during readback")
    with store.transaction() as db:
        current = store.job(db, job_id)
        if (
            current["state"] not in ("posted-unverified", "unknown")
            or current["attempt"] != attempt["attempt"]
        ):
            raise BridgeError("master reconciliation state changed")
        proof.update(
            action=payload["action"],
            kind=payload["kind"],
            reference=observed["reference"],
            response_sha256=observed["response_sha256"],
        )
        db.execute(
            "UPDATE jobs SET state='verified',txn_id=?,detail='native_master_verified' WHERE id=?",
            (proof["list_id"], job_id),
        )
        store.event(db, bridge.clock(), actor, job_id, "native_master_verified", proof)
        return store.job(db, job_id)
