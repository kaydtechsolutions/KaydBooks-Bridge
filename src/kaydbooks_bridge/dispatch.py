"""Opt-in bounded sample dispatch; approvals and native fences remain authoritative."""

import argparse
import importlib
import json
import math
import os
import time
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import BridgeError, company_policy_context, identifier, strict_keys
from .direct_sdk import company_lock
from .service import audited
from .validation import canonical, digest, money

MODULES = {
    "invoice.create": "sample_posting",
    "bill.create": "sample_bill_posting",
    "customer-payment.create": "sample_payment_posting",
    "supplier-payment.create": "sample_supplier_payment_posting",
    "customer-credit.create": "sample_credit_posting",
    "customer-credit.apply": "sample_application_posting",
    "customer-refund.create": "sample_refund_posting",
    "supplier-credit.create": "sample_supplier_credit_posting",
    "supplier-credit.apply": "sample_supplier_application_posting",
}
_active = ContextVar("bridge_dispatch_claim", default=None)


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS dispatch_profiles (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
        policy_hash TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))""")
    db.execute("""CREATE TABLE IF NOT EXISTS dispatch_occurrences (
        id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES dispatch_profiles(id),
        due_at REAL NOT NULL, result TEXT NOT NULL, UNIQUE(profile_id,due_at))""")
    db.execute("""CREATE TABLE IF NOT EXISTS dispatch_claims (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), occurrence_id TEXT NOT NULL
        REFERENCES dispatch_occurrences(id), fingerprint TEXT NOT NULL, amount TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS dispatch_results (
        job_id TEXT PRIMARY KEY REFERENCES dispatch_claims(job_id), result TEXT NOT NULL)""")
    for table in ("dispatch_occurrences", "dispatch_claims", "dispatch_results"):
        for verb in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()}
                BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'immutable dispatch evidence'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS dispatch_definition_immutable
        BEFORE UPDATE OF id,owner,definition,policy_hash ON dispatch_profiles
        BEGIN SELECT RAISE(ABORT,'immutable dispatch definition'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS dispatch_no_delete BEFORE DELETE ON dispatch_profiles
        BEGIN SELECT RAISE(ABORT,'cancel preserves dispatch evidence'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS dispatch_no_reenable BEFORE UPDATE OF enabled
        ON dispatch_profiles WHEN OLD.enabled=0 AND NEW.enabled=1
        BEGIN SELECT RAISE(ABORT,'cancelled profile cannot be reenabled'); END""")


def definition(value, policy):
    strict_keys(
        value,
        {
            "mode",
            "timezone",
            "first_run",
            "interval_seconds",
            "max_runs",
            "expires_at",
            "missed_run",
            "grace_seconds",
            "operations",
            "sources",
            "job_ids",
            "max_jobs_per_run",
            "max_jobs_total",
            "max_amount_per_job",
            "max_amount_total",
        },
    )
    if value["mode"] not in ("scheduled", "automatic") or value["missed_run"] not in (
        "skip",
        "coalesce",
    ):
        raise BridgeError("explicit dispatch mode and missed-run policy required")
    try:
        zone = ZoneInfo(value["timezone"])
        start = datetime.fromisoformat(value["first_run"])
        if start.tzinfo is None or start.utcoffset() != start.astimezone(zone).utcoffset():
            raise ValueError()
        first = start.timestamp()
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise BridgeError("offset-aware first run and matching timezone required") from exc
    for name, low, high in (
        ("interval_seconds", 60, 2678400),
        ("max_runs", 1, 1000),
        ("grace_seconds", 1, 3600),
        ("max_jobs_per_run", 1, 100),
        ("max_jobs_total", 1, 1000),
    ):
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise BridgeError("invalid bounded dispatch limits")
    if value["grace_seconds"] > value["interval_seconds"]:
        raise BridgeError("grace cannot exceed cadence")
    expiry = value["expires_at"]
    if (
        type(expiry) not in (float, int)
        or not math.isfinite(expiry)
        or not first < expiry <= first + 31622400
    ):
        raise BridgeError("bounded dispatch expiration required")
    for name, allowed in (("operations", MODULES), ("sources", policy.sources)):
        values = value[name]
        if (
            not isinstance(values, list)
            or not values
            or len(values) > 100
            or any(not isinstance(v, str) or v not in allowed for v in values)
            or len(set(values)) != len(values)
        ):
            raise BridgeError("explicit supported operation and source rules required")
    jobs = value["job_ids"]
    if (
        not isinstance(jobs, list)
        or len(jobs) > 100
        or any(not isinstance(v, str) for v in jobs)
        or len(set(jobs)) != len(jobs)
    ):
        raise BridgeError("invalid scheduled job selection")
    if bool(jobs) != (value["mode"] == "scheduled"):
        raise BridgeError("scheduled mode requires exact jobs; automatic mode uses explicit rules")
    if money(value["max_amount_per_job"]) > money(value["max_amount_total"]):
        raise BridgeError("per-job limit exceeds total budget")
    return json.loads(canonical(value)), first


def authority(config, actor, company):
    for permission in ("manage-workflows", "read", "validate", "submit", "post-sample"):
        config.authorize(actor, company, permission)


@audited
def create(bridge, token, company, profile_id, specification):
    config, actor, policy, store = bridge._context(token, company, "manage-workflows")
    authority(config, actor, company)
    identifier(profile_id)
    spec, _ = definition(specification, policy)
    if spec["expires_at"] <= bridge.clock():
        raise BridgeError("dispatch profile already expired")
    with store.transaction() as db:
        schema(db)
        if not store.verify_audit(db):
            raise BridgeError("invalid audit")
        for job_id in spec["job_ids"]:
            if store.job(db, job_id)["submitter"] != actor:
                raise BridgeError("scheduled job ownership required")
        prior = db.execute("SELECT * FROM dispatch_profiles WHERE id=?", (profile_id,)).fetchone()
        if prior:
            if (
                prior["owner"] != actor
                or prior["definition"] != canonical(spec)
                or prior["policy_hash"] != digest(company_policy_context(policy))
            ):
                raise BridgeError("dispatch profile id conflict")
            return {"id": profile_id, "enabled": bool(prior["enabled"])}
        db.execute(
            "INSERT INTO dispatch_profiles VALUES (?,?,?,?,1)",
            (profile_id, actor, canonical(spec), digest(company_policy_context(policy))),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "dispatch_profile_created",
            {"id": profile_id, "specification": spec},
        )
    return {"id": profile_id, "enabled": True}


@audited
def cancel(bridge, token, company, profile_id):
    _, actor, _, store = bridge._context(token, company, "manage-workflows")
    with store.transaction() as db:
        schema(db)
        row = db.execute("SELECT owner FROM dispatch_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None or row["owner"] != actor:
            raise BridgeError("owned dispatch profile required")
        db.execute("UPDATE dispatch_profiles SET enabled=0 WHERE id=?", (profile_id,))
        store.event(
            db, bridge.clock(), actor, None, "dispatch_profile_cancelled", {"id": profile_id}
        )
    return {"id": profile_id, "enabled": False}


@audited
def status(bridge, token, company):
    _, actor, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        schema(db)
        profiles = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM dispatch_profiles WHERE owner=? ORDER BY rowid", (actor,)
            )
        ]
        for p in profiles:
            p["definition"] = json.loads(p["definition"])
            p["occurrences"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM dispatch_occurrences WHERE profile_id=? ORDER BY due_at",
                    (p["id"],),
                )
            ]
            for occurrence in p["occurrences"]:
                occurrence["result"] = json.loads(occurrence["result"])
                occurrence["dispatch_results"] = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT r.result FROM dispatch_results r JOIN dispatch_claims c ON c.job_id=r.job_id WHERE c.occurrence_id=?",
                        (occurrence["id"],),
                    )
                ]
        return {"profiles": profiles, "production_posting": False}


def amount(job):
    payload = job["payload"]
    return (
        money(payload["total_amount"])
        if "total_amount" in payload
        else sum(
            (money(v["amount"]) for v in payload["lines"]),
            Decimal(payload.get("tax_amount", "0.00")),
        )
    )


def require(config, actor, policy, store, db, job, now):
    """Called in every native preparation AND final authorization transaction.

    Claimed jobs cannot bypass cancellation or scheduling rules via manual dispatch.
    This guard is process context, never an externally supplied authority parameter.
    """
    schema(db)
    claim = db.execute(
        """SELECT c.*, o.profile_id FROM dispatch_claims c
        JOIN dispatch_occurrences o ON o.id=c.occurrence_id WHERE c.job_id=?""",
        (job["id"],),
    ).fetchone()
    active = _active.get()
    if claim is None:
        if active is not None:
            raise BridgeError("dispatch claim missing")
        return
    if active != (str(store.path), job["id"], claim["occurrence_id"]):
        raise BridgeError("claimed job requires its original dispatch occurrence")
    profile = db.execute(
        "SELECT * FROM dispatch_profiles WHERE id=?", (claim["profile_id"],)
    ).fetchone()
    authority(config, actor, policy.id)
    spec = json.loads(profile["definition"])
    if (
        not profile["enabled"]
        or profile["owner"] != actor
        or now >= spec["expires_at"]
        or profile["policy_hash"] != digest(company_policy_context(policy))
    ):
        raise BridgeError("dispatch profile cancelled, expired or policy changed")
    if (
        claim["fingerprint"] != job["fingerprint"]
        or claim["amount"] != str(amount(job))
        or db.execute("SELECT 1 FROM dispatch_results WHERE job_id=?", (job["id"],)).fetchone()
    ):
        raise BridgeError("dispatch claim changed or already completed")


def plan(bridge, config, actor, policy, store, db, profile, now):
    spec, first = definition(json.loads(profile["definition"]), policy)
    if now < first or now >= spec["expires_at"]:
        return
    if profile["policy_hash"] != digest(company_policy_context(policy)):
        return
    latest = min(int((now - first) // spec["interval_seconds"]), spec["max_runs"] - 1)
    recorded = {
        r[0]
        for r in db.execute(
            "SELECT due_at FROM dispatch_occurrences WHERE profile_id=?", (profile["id"],)
        )
    }
    # Persist every missed occurrence. Coalescing executes at most the latest one.
    for index in range(latest + 1):
        due = first + index * spec["interval_seconds"]
        if due in recorded:
            continue
        skipped = index != latest or (
            spec["missed_run"] == "skip" and now - due > spec["grace_seconds"]
        )
        occurrence = digest({"profile": profile["id"], "due": due})
        selected, held = [], []
        spent = db.execute(
            """SELECT c.amount FROM dispatch_claims c JOIN dispatch_occurrences o
            ON o.id=c.occurrence_id WHERE o.profile_id=?""",
            (profile["id"],),
        ).fetchall()
        total, count = sum((Decimal(r[0]) for r in spent), Decimal(0)), len(spent)
        if not skipped:
            for row in db.execute(
                "SELECT id FROM jobs WHERE state='queued' AND submitter=? ORDER BY rowid", (actor,)
            ).fetchall():
                job = store.job(db, row["id"])
                if db.execute(
                    "SELECT 1 FROM dispatch_claims WHERE job_id=?", (job["id"],)
                ).fetchone():
                    continue
                if spec["job_ids"] and job["id"] not in spec["job_ids"]:
                    continue
                if (
                    job["operation"] not in spec["operations"]
                    or job["source"]["namespace"] not in spec["sources"]
                ):
                    continue
                value = amount(job)
                if (
                    value > money(spec["max_amount_per_job"])
                    or total + value > money(spec["max_amount_total"])
                    or count >= spec["max_jobs_total"]
                ):
                    held.append({"job_id": job["id"], "reason": "dispatch_limit_requires_review"})
                    continue
                if len(selected) >= spec["max_jobs_per_run"]:
                    break
                selected.append((job, str(value)))
                total += value
                count += 1
        result = {"skipped": skipped, "jobs": [j["id"] for j, _ in selected], "held": held}
        db.execute(
            "INSERT INTO dispatch_occurrences VALUES (?,?,?,?)",
            (occurrence, profile["id"], due, canonical(result)),
        )
        for job, value in selected:
            db.execute(
                "INSERT INTO dispatch_claims VALUES (?,?,?,?)",
                (job["id"], occurrence, job["fingerprint"], value),
            )
        store.event(
            db, now, actor, None, "dispatch_occurrence_planned", {"id": occurrence, **result}
        )


@audited
def tick(bridge, token, company, *, dispatchers=None):
    """Run due owned work once. Adapter injection is Python-test-only, never an API field."""
    config, actor, policy, store = bridge._context(token, company, "manage-workflows")
    authority(config, actor, company)
    results = []
    with company_lock(store.path.with_suffix(".dispatch.lock")):
        with store.transaction() as db:
            schema(db)
            if not store.verify_audit(db):
                raise BridgeError("invalid audit")
            if db.execute("SELECT paused FROM control").fetchone()[0]:
                raise BridgeError("company paused")
            profiles = db.execute(
                "SELECT * FROM dispatch_profiles WHERE owner=? AND enabled=1 ORDER BY rowid",
                (actor,),
            ).fetchall()
            for profile in profiles:
                plan(bridge, config, actor, policy, store, db, profile, bridge.clock())
            pending = db.execute(
                """SELECT c.* FROM dispatch_claims c JOIN dispatch_occurrences o ON o.id=c.occurrence_id
                JOIN dispatch_profiles p ON p.id=o.profile_id LEFT JOIN dispatch_results r ON r.job_id=c.job_id
                WHERE p.owner=? AND r.job_id IS NULL ORDER BY o.due_at,c.rowid""",
                (actor,),
            ).fetchall()
        for claim in pending:
            marker = _active.set((str(store.path), claim["job_id"], claim["occurrence_id"]))
            try:
                current_config, current_actor, current_policy, _ = bridge._context(
                    token, company, "manage-workflows"
                )
                with store.transaction() as db:
                    job = store.job(db, claim["job_id"])
                    require(
                        current_config,
                        current_actor,
                        current_policy,
                        store,
                        db,
                        job,
                        bridge.clock(),
                    )
                if job["state"] != "queued":
                    result = {
                        "job_id": job["id"],
                        "state": job["state"],
                        "action": "reconcile_without_resend"
                        if job["state"] != "verified"
                        else "already_verified",
                    }
                else:
                    dispatch = (
                        dispatchers[job["operation"]]
                        if dispatchers is not None
                        else importlib.import_module(
                            "." + MODULES[job["operation"]], __package__
                        ).post
                    )
                    posted = dispatch(bridge, token, company, job["id"])
                    result = {
                        "job_id": job["id"],
                        "state": posted["state"],
                        "action": "native_dispatch",
                    }
            except (BridgeError, OSError, RuntimeError):
                result = {"job_id": claim["job_id"], "action": "held_for_review"}
            finally:
                _active.reset(marker)
            with store.transaction() as db:
                result["state"] = store.job(db, claim["job_id"])["state"]
                db.execute(
                    "INSERT INTO dispatch_results VALUES (?,?)",
                    (claim["job_id"], canonical(result)),
                )
                store.event(
                    db, bridge.clock(), actor, claim["job_id"], "dispatch_occurrence_result", result
                )
            results.append(result)
    return {"results": results, "production_posting": False}


def main(argv=None):
    """An explicitly started project worker; no worker starts on company setup."""
    parser = argparse.ArgumentParser(description="Run bounded approved sample dispatch profiles")
    parser.add_argument("--company", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    from .deployment import load_secret_file
    from .service import Bridge

    config_path = os.environ.get("KAYDBOOKS_CONFIG", "")
    secret_file = os.environ.get("KAYDBOOKS_QBWC_SECRET_FILE")
    if secret_file:
        load_secret_file(secret_file)
    bridge = Bridge(config_path)
    while True:
        try:
            # Re-read the credential on every tick; native dispatch authenticates again.
            if secret_file:
                load_secret_file(secret_file)
            result = tick(bridge, os.environ.get(args.token_env, ""), args.company)
            print(canonical(result), flush=True)
        except (BridgeError, OSError, ValueError):
            print(canonical({"action": "held_for_review", "production_posting": False}), flush=True)
            if args.once:
                return 2
        if args.once:
            return 0
        time.sleep(5)
