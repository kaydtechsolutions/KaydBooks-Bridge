"""Immutable draft lineage and canonical duplicate reservations across corrections."""

import re

from .config import BridgeError, strict_keys
from .validation import canonical


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS job_revisions (
        parent_id TEXT PRIMARY KEY REFERENCES jobs(id), child_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
        root_id TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 100),
        reason TEXT NOT NULL, UNIQUE(root_id,number))""")
    db.execute("""CREATE TABLE IF NOT EXISTS canonical_job_keys (
        kind TEXT NOT NULL CHECK(kind IN ('source','business')), key TEXT NOT NULL,
        root_id TEXT NOT NULL REFERENCES jobs(id), PRIMARY KEY(kind,key))""")
    for table in ("job_revisions", "canonical_job_keys"):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable revision lineage'); END""")
    for kind, column in (("source", "source_key"), ("business", "business_key")):
        db.execute(
            f"""INSERT OR IGNORE INTO canonical_job_keys
            SELECT ?,j.{column},j.id FROM jobs j WHERE NOT EXISTS
            (SELECT 1 FROM job_revisions r WHERE r.child_id=j.id)""",
            (kind,),
        )
    db.execute("""CREATE TRIGGER IF NOT EXISTS superseded_job_immutable
        BEFORE UPDATE ON jobs WHEN EXISTS (SELECT 1 FROM job_revisions WHERE parent_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'superseded revision cannot change or dispatch'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS revision_insert_guard BEFORE INSERT ON job_revisions
        WHEN NOT EXISTS (SELECT 1 FROM jobs p JOIN jobs c ON c.id=NEW.child_id WHERE p.id=NEW.parent_id
            AND p.id!=c.id AND p.state='draft' AND p.detail='superseded' AND p.attempt IS NULL AND p.txn_id IS NULL
            AND p.approval_by IS NULL AND p.approval_hash IS NULL AND c.state='draft' AND c.attempt IS NULL
            AND c.submitter=p.submitter AND c.operation=p.operation
            AND NEW.number=COALESCE((SELECT number+1 FROM job_revisions WHERE child_id=p.id),1)
            AND NEW.root_id=COALESCE((SELECT root_id FROM job_revisions WHERE child_id=p.id),p.id))
        BEGIN SELECT RAISE(ABORT,'invalid owned undispatched revision'); END""")


def root_of(db, job_id):
    row = db.execute("SELECT root_id FROM job_revisions WHERE child_id=?", (job_id,)).fetchone()
    return row[0] if row else job_id


def active(db, root):
    row = db.execute(
        "SELECT child_id FROM job_revisions WHERE root_id=? ORDER BY number DESC LIMIT 1", (root,)
    ).fetchone()
    return row[0] if row else root


def resolve(db, store, actor, envelope, fingerprint, source_key, business_key):
    roots = {
        r[0]
        for r in db.execute(
            "SELECT root_id FROM canonical_job_keys WHERE (kind='source' AND key=?) OR (kind='business' AND key=?)",
            (source_key, business_key),
        )
    }
    idem = db.execute(
        "SELECT job_id FROM idempotency_keys WHERE key=?", (envelope["idempotency_key"],)
    ).fetchone()
    if idem:
        roots.add(root_of(db, idem[0]))
    request = envelope.get("revision_of")
    info = None
    if request is not None:
        strict_keys(request, {"parent_id", "parent_fingerprint", "reason"})
        if not isinstance(request["parent_id"], str) or not re.fullmatch(
            r"[a-f0-9]{32}", request["parent_id"]
        ):
            raise BridgeError("exact parent job required")
        if (
            not isinstance(request["reason"], str)
            or not 1 <= len(request["reason"]) <= 500
            or not request["reason"].strip()
        ):
            raise BridgeError("bounded correction reason required")
        parent = store.job(db, request["parent_id"])
        root = root_of(db, parent["id"])
        if (
            parent["submitter"] != actor
            or parent["operation"] != envelope["operation"]
            or parent["fingerprint"] != request["parent_fingerprint"]
        ):
            raise BridgeError("owned parent and exact revision fingerprint required")
        if any(r != root for r in roots):
            raise BridgeError("revision conflicts with another canonical transaction")
        link = db.execute(
            "SELECT * FROM job_revisions WHERE parent_id=?", (parent["id"],)
        ).fetchone()
        if link:
            if (
                not idem
                or idem[0] != link["child_id"]
                or active(db, root) != link["child_id"]
                or request["reason"] != link["reason"]
            ):
                raise BridgeError("stale parent revision; use the current draft")
            roots = {root}
        else:
            if (
                idem
                or parent["state"] not in ("draft", "validated", "queued")
                or parent["attempt"] is not None
                or parent["txn_id"] is not None
                or active(db, root) != parent["id"]
            ):
                raise BridgeError("only the current undispatched draft can be corrected")
            if fingerprint == parent["fingerprint"]:
                raise BridgeError("correction must change extracted evidence")
            prior = db.execute(
                "SELECT number FROM job_revisions WHERE child_id=?", (parent["id"],)
            ).fetchone()
            number = prior[0] + 1 if prior else 1
            if number > 100:
                raise BridgeError("revision limit reached")
            info = {
                "parent_id": parent["id"],
                "root_id": root,
                "number": number,
                "reason": request["reason"],
                "source_key": source_key,
                "business_key": business_key,
            }
            return (
                [],
                info,
                canonical(["revision", root, number, source_key]),
                canonical(["revision", root, number, business_key]),
            )
    matches = [
        dict(db.execute("SELECT id,fingerprint FROM jobs WHERE id=?", (active(db, r),)).fetchone())
        for r in sorted(roots)
    ]
    return matches, info, source_key, business_key


def record(db, job_id, info, source_key, business_key):
    root = info["root_id"] if info else job_id
    if info:
        db.execute(
            "UPDATE jobs SET state='draft',approval_by=NULL,approval_hash=NULL,detail='superseded' WHERE id=?",
            (info["parent_id"],),
        )
        db.execute(
            "INSERT INTO job_revisions VALUES (?,?,?,?,?)",
            (info["parent_id"], job_id, root, info["number"], info["reason"]),
        )
        source_key, business_key = info["source_key"], info["business_key"]
    for kind, key in (("source", source_key), ("business", business_key)):
        existing = db.execute(
            "SELECT root_id FROM canonical_job_keys WHERE kind=? AND key=?", (kind, key)
        ).fetchone()
        if existing and existing[0] != root:
            raise BridgeError("canonical revision reservation conflicts")
        db.execute("INSERT OR IGNORE INTO canonical_job_keys VALUES (?,?,?)", (kind, key, root))


def decorate(db, result):
    row = db.execute("SELECT * FROM job_revisions WHERE child_id=?", (result["id"],)).fetchone()
    if row:
        result["revision"] = dict(row)
    child = db.execute(
        "SELECT child_id FROM job_revisions WHERE parent_id=?", (result["id"],)
    ).fetchone()
    if child:
        result["state"] = "superseded"
        result["superseded_by"] = child[0]
