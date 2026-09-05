"""Per-company SQLite durability. No in-memory queue or automatic write retry."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import BridgeError, identifier, outside_repository
from .validation import canonical, digest

STATES = (
    "draft",
    "validated",
    "queued",
    "in-flight",
    "posted-unverified",
    "verified",
    "blocked",
    "failed",
    "unknown",
)


class Store:
    def __init__(self, root: Path, company: str):
        self.company = identifier(company)
        root = outside_repository(root)
        folder = (root / company).resolve()
        if not folder.is_relative_to(root):
            raise BridgeError("company state path escapes private root")
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "jobs.sqlite3"
        if self.path.is_symlink():
            raise BridgeError("company database must not be a symbolic link")
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            existing = dict(db.execute("SELECT key, value FROM metadata").fetchall())
            if existing and existing != {"schema_version": "1", "company": company}:
                raise BridgeError("database version or company binding mismatch")
            db.executemany(
                "INSERT OR IGNORE INTO metadata VALUES (?, ?)",
                [("schema_version", "1"), ("company", company)],
            )
            db.execute(f"""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, source_key TEXT NOT NULL UNIQUE,
                business_key TEXT NOT NULL UNIQUE, operation TEXT NOT NULL,
                submitter TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN {STATES}),
                payload TEXT NOT NULL, source TEXT NOT NULL,
                approval_by TEXT, approval_hash TEXT, attempt TEXT, lease_until REAL,
                txn_id TEXT, detail TEXT NOT NULL DEFAULT '')""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_write
                ON jobs ((1)) WHERE state IN ('in-flight', 'posted-unverified', 'unknown')""")
            db.execute("""CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id))""")
            db.execute("INSERT OR IGNORE INTO idempotency_keys SELECT idempotency_key,id FROM jobs")
            db.execute("""CREATE TABLE IF NOT EXISTS audit (
                sequence INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
                job_id TEXT, event TEXT NOT NULL, data TEXT NOT NULL,
                previous_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)"
            )
            db.execute("INSERT OR IGNORE INTO control VALUES (1, 0)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, at: float, actor: str, job_id: str | None, event: str, data: dict):
        at = float(at)
        last = db.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = last[0] if last else "0" * 64
        value = {
            "company": self.company,
            "at": at,
            "actor": actor,
            "job_id": job_id,
            "event": event,
            "data": data,
            "previous_hash": previous,
        }
        db.execute(
            "INSERT INTO audit (at,actor,job_id,event,data,previous_hash,hash) VALUES (?,?,?,?,?,?,?)",
            (at, actor, job_id, event, canonical(data), previous, digest(value)),
        )

    @staticmethod
    def job(db, job_id: str) -> dict:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise BridgeError("job not found")
        result = dict(row)
        for key in ("payload", "source"):
            result[key] = json.loads(result[key])
        return result

    def verify_audit(self, db) -> bool:
        previous = "0" * 64
        for row in db.execute("SELECT * FROM audit ORDER BY sequence"):
            value = {
                "company": self.company,
                "at": row["at"],
                "actor": row["actor"],
                "job_id": row["job_id"],
                "event": row["event"],
                "data": json.loads(row["data"]),
                "previous_hash": previous,
            }
            if row["previous_hash"] != previous or row["hash"] != digest(value):
                return False
            previous = row["hash"]
        return True
