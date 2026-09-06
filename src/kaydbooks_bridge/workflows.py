"""Local optional workflows; no message delivery or accounting dispatch."""

import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import BridgeError, identifier
from .service import audited
from .validation import canonical, digest


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS workflow_schedules (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, definition TEXT NOT NULL,
        policy_hash TEXT NOT NULL, next_at REAL NOT NULL, remaining INTEGER NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))""")
    db.execute("""CREATE TABLE IF NOT EXISTS workflow_occurrences (
        id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, due_at REAL NOT NULL,
        result TEXT NOT NULL, UNIQUE(schedule_id,due_at))""")
    db.execute("""CREATE TABLE IF NOT EXISTS workflow_outbox (
        id TEXT PRIMARY KEY, content TEXT NOT NULL,
        delivery TEXT NOT NULL CHECK(delivery='local-only'))""")
    db.execute("""CREATE TABLE IF NOT EXISTS workflow_memory (
        name TEXT NOT NULL, version INTEGER NOT NULL, owner TEXT NOT NULL,
        value TEXT NOT NULL, expires_at REAL NOT NULL, provenance TEXT NOT NULL,
        PRIMARY KEY(name,version))""")
    db.execute("""CREATE TABLE IF NOT EXISTS workflow_delegations (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), owner TEXT NOT NULL,
        assignee TEXT NOT NULL)""")
    for table in (
        "workflow_occurrences",
        "workflow_outbox",
        "workflow_memory",
        "workflow_delegations",
    ):
        for operation in ("UPDATE", "DELETE"):
            db.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable workflow evidence'); END"
            )
    db.execute("""CREATE TRIGGER IF NOT EXISTS schedule_definition_immutable
        BEFORE UPDATE OF owner,definition,policy_hash ON workflow_schedules
        BEGIN SELECT RAISE(ABORT,'immutable schedule definition'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS schedule_no_delete BEFORE DELETE ON workflow_schedules
        BEGIN SELECT RAISE(ABORT,'schedule cancellation preserves evidence'); END""")


def projection(db):
    cards = [
        {key: row[key] for key in ("id", "state", "submitter", "detail")}
        for row in db.execute("SELECT id,state,submitter,detail FROM jobs ORDER BY rowid")
    ]
    counts = {}
    for card in cards:
        counts[card["state"]] = counts.get(card["state"], 0) + 1
    return {"cards": cards, "counts": counts, "editable": False}


@audited
def board(bridge, token, company):
    _, _, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        return projection(db)


@audited
def schedule(
    bridge,
    token,
    company,
    schedule_id,
    timezone,
    first_run,
    interval_seconds,
    max_runs,
    dependencies,
):
    config, actor, policy, store = bridge._context(token, company, "manage-workflows")
    config.authorize(actor, company, "read")
    identifier(schedule_id)
    if not isinstance(timezone, str) or not isinstance(first_run, str):
        raise BridgeError("explicit timezone and offset-aware first run required")
    try:
        zone = ZoneInfo(timezone)
        start = datetime.fromisoformat(first_run)
        if start.tzinfo is None or start.utcoffset() != start.astimezone(zone).utcoffset():
            raise ValueError()
        at = start.timestamp()
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise BridgeError("invalid timezone or ambiguous first-run offset") from exc
    if (
        type(interval_seconds) is not int
        or not 60 <= interval_seconds <= 2678400
        or type(max_runs) is not int
        or not 1 <= max_runs <= 1000
    ):
        raise BridgeError("invalid bounded schedule cadence")
    if (
        not isinstance(dependencies, list)
        or len(dependencies) > 50
        or any(not isinstance(item, str) for item in dependencies)
        or len(set(dependencies)) != len(dependencies)
    ):
        raise BridgeError("invalid job dependencies")
    definition = canonical(
        {
            "timezone": timezone,
            "first_run": first_run,
            "interval_seconds": interval_seconds,
            "max_runs": max_runs,
            "dependencies": dependencies,
            "operation": "board.snapshot",
        }
    )
    with store.transaction() as db:
        schema(db)
        for job_id in dependencies:
            store.job(db, job_id)
        existing = db.execute(
            "SELECT * FROM workflow_schedules WHERE id=?", (schedule_id,)
        ).fetchone()
        if existing:
            if existing["definition"] != definition or existing["owner"] != actor:
                raise BridgeError("schedule id conflict")
            return dict(existing)
        db.execute(
            "INSERT INTO workflow_schedules VALUES (?,?,?,?,?,?,1)",
            (schedule_id, actor, definition, digest(asdict(policy)), at, max_runs),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "workflow_scheduled",
            {"id": schedule_id, "operation": "board.snapshot"},
        )
        return {"id": schedule_id, "enabled": True, "next_at": at}


@audited
def cancel(bridge, token, company, schedule_id):
    _, actor, _, store = bridge._context(token, company, "manage-workflows")
    with store.transaction() as db:
        schema(db)
        row = db.execute(
            "SELECT owner FROM workflow_schedules WHERE id=?", (schedule_id,)
        ).fetchone()
        if row is None or row["owner"] != actor:
            raise BridgeError("owned schedule required")
        db.execute("UPDATE workflow_schedules SET enabled=0 WHERE id=?", (schedule_id,))
        store.event(db, bridge.clock(), actor, None, "workflow_cancelled", {"id": schedule_id})
        return {"id": schedule_id, "enabled": False}


@audited
def tick(bridge, token, company):
    config, actor, policy, store = bridge._context(token, company, "manage-workflows")
    config.authorize(actor, company, "read")
    completed, held = [], []
    with store.transaction() as db:
        schema(db)
        if not store.verify_audit(db):
            raise BridgeError("invalid workflow audit")
        if db.execute("SELECT paused FROM control").fetchone()[0]:
            return {"completed": [], "held": ["company-paused"]}
        rows = db.execute(
            "SELECT * FROM workflow_schedules WHERE enabled=1 AND remaining>0 AND next_at<=? ORDER BY next_at,id LIMIT 100",
            (bridge.clock(),),
        ).fetchall()
        for row in rows:
            definition = json.loads(row["definition"])
            try:
                config.authorize(row["owner"], company, "manage-workflows")
                config.authorize(row["owner"], company, "read")
                if row["policy_hash"] != digest(asdict(policy)) or any(
                    store.job(db, job)["state"] != "verified" for job in definition["dependencies"]
                ):
                    raise BridgeError("schedule context or dependencies held")
            except BridgeError:
                held.append(row["id"])
                continue
            occurrence = digest([company, row["id"], row["next_at"]])
            result = {
                "company": company,
                "counts": projection(db)["counts"],
                "as_of": bridge.clock(),
                "derived": True,
            }
            db.execute(
                "INSERT INTO workflow_occurrences VALUES (?,?,?,?)",
                (occurrence, row["id"], row["next_at"], canonical(result)),
            )
            # Local notification preview only: contains counts, never source or accounting values.
            db.execute(
                "INSERT INTO workflow_outbox VALUES (?,?,'local-only')",
                (occurrence, canonical(result)),
            )
            db.execute(
                "UPDATE workflow_schedules SET next_at=next_at+?,remaining=remaining-1,enabled=? WHERE id=?",
                (definition["interval_seconds"], int(row["remaining"] > 1), row["id"]),
            )
            store.event(
                db,
                bridge.clock(),
                actor,
                None,
                "workflow_completed",
                {"id": row["id"], "occurrence": occurrence},
            )
            completed.append(occurrence)
    return {"completed": completed, "held": held, "accounting_writes": 0, "external_deliveries": 0}


@audited
def remember(bridge, token, company, name, value, expires_at, provenance, expected_version):
    _, actor, _, store = bridge._context(token, company, "manage-workflows")
    # Preferences cannot smuggle IDs, balances, grants or executable actions into policy.
    if (
        name not in {"display-label", "preferred-report"}
        or not isinstance(value, str)
        or not 1 <= len(value) <= 120
        or not isinstance(provenance, str)
        or not 1 <= len(provenance) <= 256
    ):
        raise BridgeError("unsupported preference")
    if (
        type(expires_at) not in (int, float)
        or not bridge.clock() < expires_at <= bridge.clock() + 31536000
        or type(expected_version) is not int
    ):
        raise BridgeError("invalid preference expiry/version")
    if name == "preferred-report" and value != "verified-invoice-register":
        raise BridgeError("unsupported report preference")
    with store.transaction() as db:
        schema(db)
        version = db.execute(
            "SELECT COALESCE(MAX(version),0) FROM workflow_memory WHERE name=?", (name,)
        ).fetchone()[0]
        if version != expected_version:
            raise BridgeError("preference version changed")
        db.execute(
            "INSERT INTO workflow_memory VALUES (?,?,?,?,?,?)",
            (name, version + 1, actor, value, expires_at, provenance),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "preference_recorded",
            {"name": name, "version": version + 1},
        )
        return {"name": name, "version": version + 1}


@audited
def memory(bridge, token, company):
    _, _, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        schema(db)
        return {
            "preferences": [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM workflow_memory m WHERE version=(SELECT MAX(version) FROM workflow_memory WHERE name=m.name) AND expires_at>?",
                    (bridge.clock(),),
                )
            ],
            "authority": False,
        }


@audited
def delegate(bridge, token, company, job_id, assignee):
    config, actor, _, store = bridge._context(token, company, "manage-workflows")
    config.authorize(assignee, company, "read")
    config.authorize(assignee, company, "validate")
    with store.transaction() as db:
        schema(db)
        job = store.job(db, job_id)
        if job["submitter"] != actor:
            raise BridgeError("delegation requires canonical job ownership")
        row = db.execute("SELECT * FROM workflow_delegations WHERE job_id=?", (job_id,)).fetchone()
        if row and row["assignee"] != assignee:
            raise BridgeError("delegation already assigned")
        db.execute(
            "INSERT OR IGNORE INTO workflow_delegations VALUES (?,?,?)", (job_id, actor, assignee)
        )
        store.event(db, bridge.clock(), actor, job_id, "job_delegated", {"assignee": assignee})
        return {"job_id": job_id, "assignee": assignee, "additional_authority": False}
