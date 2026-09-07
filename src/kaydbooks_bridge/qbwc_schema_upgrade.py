"""Transactional expansion of the fixed QBWC read-operation allowlist."""

from .qbwc_contracts import CONTRACTS, OPERATIONS_SQL


def expand_read_operations(db):
    sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='qbwc_invoice_jobs'"
    ).fetchone()[0]
    if all("'" + operation + "'" in sql for operation in CONTRACTS):
        return
    # Other table triggers refer to this table; retain them across the rebuild.
    triggers = db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
        "AND instr(sql,'qbwc_invoice_jobs')>0"
    ).fetchall()
    for name, _ in triggers:
        db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    db.execute(f"""CREATE TABLE qbwc_invoice_jobs_next (
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, connector TEXT NOT NULL,
        payload TEXT NOT NULL, context_hash TEXT NOT NULL, ticket TEXT UNIQUE,
        txn_id TEXT, operation TEXT NOT NULL DEFAULT 'invoice.create'
        CHECK(operation IN ({OPERATIONS_SQL})))""")
    # Preserve rowid too: browser evidence lookup orders reads by insertion order.
    db.execute("""INSERT INTO qbwc_invoice_jobs_next
        (rowid,id,actor,connector,payload,context_hash,ticket,txn_id,operation)
        SELECT rowid,id,actor,connector,payload,context_hash,ticket,txn_id,operation
        FROM qbwc_invoice_jobs""")
    db.execute("DROP TABLE qbwc_invoice_jobs")
    db.execute("ALTER TABLE qbwc_invoice_jobs_next RENAME TO qbwc_invoice_jobs")
    db.execute("""CREATE UNIQUE INDEX one_pending_invoice_job
        ON qbwc_invoice_jobs(connector) WHERE ticket IS NULL""")
    for _, statement in triggers:
        db.execute(statement)
    for name in (
        "jobs_state_transition_guard",
        "qbwc_not_dispatched_insert_guard",
        "qbwc_contract_attempt_guard",
    ):
        db.execute("DROP TRIGGER IF EXISTS " + name)
