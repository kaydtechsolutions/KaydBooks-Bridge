"""Older read history and SQL protections survive operation-list expansion."""

import sqlite3

import pytest

from kaydbooks_bridge.qbwc_schema_upgrade import expand_read_operations


@pytest.mark.parametrize("rollback", [False, True])
def test_legacy_invoice_bill_read_upgrade_is_atomic(tmp_path, rollback):
    with sqlite3.connect(tmp_path / "legacy.sqlite3", isolation_level=None) as db:
        db.execute("""CREATE TABLE qbwc_invoice_jobs (
            id TEXT PRIMARY KEY,actor TEXT NOT NULL,connector TEXT NOT NULL,
            payload TEXT NOT NULL,context_hash TEXT NOT NULL,ticket TEXT UNIQUE,
            txn_id TEXT,operation TEXT NOT NULL DEFAULT 'invoice.create'
            CHECK(operation IN ('invoice.create','bill.create')))""")
        db.execute("""CREATE UNIQUE INDEX one_pending_invoice_job
            ON qbwc_invoice_jobs(connector) WHERE ticket IS NULL""")
        db.executemany(
            "INSERT INTO qbwc_invoice_jobs(rowid,id,actor,connector,payload,context_hash,ticket,operation) VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    17,
                    "old-invoice",
                    "operator",
                    "connector",
                    "{}",
                    "hash-a",
                    "ticket-a",
                    "invoice.create",
                ),
                (52, "old-bill", "operator", "connector", "{}", "hash-b", None, "bill.create"),
            ],
        )
        db.execute("""CREATE TRIGGER keep_read_history BEFORE DELETE ON qbwc_invoice_jobs
            BEGIN SELECT RAISE(ABORT,'immutable history'); END""")
        db.execute("""CREATE TRIGGER keep_operation BEFORE UPDATE OF operation ON qbwc_invoice_jobs
            BEGIN SELECT RAISE(ABORT,'immutable operation'); END""")
        db.execute("CREATE TABLE qbwc_account_jobs(id TEXT,connector TEXT,ticket TEXT)")
        db.execute("""CREATE TRIGGER prevent_parallel_read BEFORE INSERT ON qbwc_account_jobs
            WHEN EXISTS(SELECT 1 FROM qbwc_invoice_jobs WHERE connector=NEW.connector AND ticket IS NULL)
            BEGIN SELECT RAISE(ABORT,'pending read'); END""")
        before = db.execute("SELECT rowid,* FROM qbwc_invoice_jobs ORDER BY rowid").fetchall()
        db.execute("BEGIN IMMEDIATE")
        expand_read_operations(db)
        db.execute("ROLLBACK" if rollback else "COMMIT")
        assert (
            db.execute("SELECT rowid,* FROM qbwc_invoice_jobs ORDER BY rowid").fetchall() == before
        )
        if not rollback:
            expand_read_operations(db)  # repeat initialization is idempotent
        with pytest.raises(sqlite3.IntegrityError, match="immutable history"):
            db.execute("DELETE FROM qbwc_invoice_jobs WHERE id='old-bill'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable operation"):
            db.execute(
                "UPDATE qbwc_invoice_jobs SET operation='invoice.create' WHERE id='old-bill'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="pending read"):
            db.execute("INSERT INTO qbwc_account_jobs VALUES('new','connector',NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO qbwc_invoice_jobs(id,actor,connector,payload,context_hash) VALUES('parallel','operator','connector','{}','hash')"
            )
        insertion = "INSERT INTO qbwc_invoice_jobs(id,actor,connector,payload,context_hash,operation) VALUES('new','operator','other','{}','hash',?)"
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute(insertion, ("arbitrary.write",))
        if rollback:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                db.execute(insertion, ("customer-payment.create",))
        else:
            db.execute(insertion, ("customer-payment.create",))
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
