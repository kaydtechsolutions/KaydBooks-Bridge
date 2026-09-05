"""Durable synthetic external ledger. This module has no network or SDK client.

Its interface is a bridge-internal test contract, NOT a Hermes or QuickBooks API.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from .config import BridgeError, Company
from .store import Store
from .validation import canonical


class SyntheticLedger:
    def __init__(self, store: Store, company: Company):
        self.company = company
        self.path = store.path.parent / "synthetic-ledger.sqlite3"
        if self.path.is_symlink():
            raise BridgeError("synthetic ledger must not be a symbolic link")
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)")
            if not db.execute("SELECT 1 FROM identity").fetchone():
                db.execute("INSERT INTO identity VALUES (?)", (company.simulation_identity,))
            db.execute(
                "CREATE TABLE IF NOT EXISTS records (txn_id TEXT PRIMARY KEY, ref TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def _connect(self):
        # contextlib.closing is unnecessary here only if explicitly closed; use our
        # context manager below so Windows can release files immediately after tests.
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()

        return connection()

    def identity(self) -> str:
        with self._connect() as db:
            return db.execute("SELECT value FROM identity").fetchone()[0]

    def masters_valid(self, payload: dict) -> bool:
        return payload["customer_id"] in self.company.customers and all(
            line["item_id"] in self.company.items for line in payload["lines"]
        )

    def find(self, payload: dict) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT txn_id,payload FROM records WHERE ref=?",
                (payload["ref_number"].casefold(),),
            ).fetchall()
        return [{"txn_id": row[0], "payload": json.loads(row[1])} for row in rows]

    def write(self, payload: dict) -> str:
        txn_id = "sim-" + uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO records VALUES (?,?,?)",
                (txn_id, payload["ref_number"].casefold(), canonical(payload)),
            )
        return txn_id

    def read(self, txn_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM records WHERE txn_id=?", (txn_id,)).fetchone()
        return json.loads(row[0]) if row else None
