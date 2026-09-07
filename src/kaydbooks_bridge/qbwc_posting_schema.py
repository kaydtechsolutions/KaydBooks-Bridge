"""Durable invoice dispatch envelopes and append-only QBWC exchange evidence."""


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS qbwc_invoice_attempts (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
        connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
        request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='qbwc_contract_attempt_guard'"
    ).fetchone():
        db.execute("DROP TRIGGER IF EXISTS qbwc_invoice_attempt_guard")
    db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_contract_attempt_guard
        BEFORE INSERT ON qbwc_invoice_attempts WHEN NOT EXISTS
        (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation IN ('invoice.create','bill.create')
         AND state='queued' AND submitter=NEW.actor AND attempt IS NULL)
        BEGIN SELECT RAISE(ABORT,'QBWC dispatch requires owned queued invoice'); END""")
    db.execute("""CREATE TABLE IF NOT EXISTS qbwc_invoice_runs (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES qbwc_invoice_attempts(job_id),
        recovery INTEGER NOT NULL CHECK(recovery IN (0,1)), ticket TEXT UNIQUE,
        phase TEXT NOT NULL CHECK(phase IN ('preflight','write','find','lookup','done','held')),
        created_at REAL NOT NULL, context TEXT, txn_id TEXT)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS qbwc_invoice_one_run
        ON qbwc_invoice_runs(job_id) WHERE phase NOT IN ('done','held')""")
    db.execute("""CREATE TABLE IF NOT EXISTS qbwc_invoice_steps (
        run_id TEXT NOT NULL REFERENCES qbwc_invoice_runs(id), phase TEXT NOT NULL,
        request TEXT NOT NULL, request_hash TEXT NOT NULL, sent_at REAL NOT NULL,
        PRIMARY KEY(run_id,phase))""")
    db.execute("""CREATE TABLE IF NOT EXISTS qbwc_invoice_responses (
        run_id TEXT NOT NULL, phase TEXT NOT NULL, response TEXT NOT NULL,
        response_hash TEXT NOT NULL, callback_hash TEXT NOT NULL, result INTEGER NOT NULL,
        PRIMARY KEY(run_id,phase), FOREIGN KEY(run_id,phase)
        REFERENCES qbwc_invoice_steps(run_id,phase))""")
    for table in ("qbwc_invoice_attempts", "qbwc_invoice_steps", "qbwc_invoice_responses"):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable QBWC posting evidence'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_invoice_run_identity
        BEFORE UPDATE OF id,job_id,recovery,created_at,ticket,context ON qbwc_invoice_runs
        WHEN OLD.id IS NOT NEW.id OR OLD.job_id IS NOT NEW.job_id
          OR OLD.recovery IS NOT NEW.recovery OR OLD.created_at IS NOT NEW.created_at
          OR (OLD.ticket IS NOT NULL AND OLD.ticket IS NOT NEW.ticket)
          OR (OLD.context IS NOT NULL AND OLD.context IS NOT NEW.context)
        BEGIN SELECT RAISE(ABORT,'immutable QBWC run binding'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_invoice_run_no_delete
        BEFORE DELETE ON qbwc_invoice_runs
        BEGIN SELECT RAISE(ABORT,'QBWC runs preserve history'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_invoice_phase_guard
        BEFORE UPDATE OF phase ON qbwc_invoice_runs WHEN NOT (
            OLD.phase=NEW.phase
            OR (OLD.phase='preflight' AND NEW.phase IN ('write','lookup','held'))
            OR (OLD.phase IN ('write','find') AND NEW.phase IN ('lookup','held'))
            OR (OLD.phase='lookup' AND NEW.phase IN ('done','held')))
        BEGIN SELECT RAISE(ABORT,'invalid QBWC posting phase'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_invoice_recovery_read_only
        BEFORE INSERT ON qbwc_invoice_steps WHEN NEW.phase='write' AND (
            NOT EXISTS (SELECT 1 FROM qbwc_invoice_runs WHERE id=NEW.run_id
                        AND phase='write' AND recovery=0)
            OR EXISTS (SELECT 1 FROM qbwc_invoice_steps s
                       JOIN qbwc_invoice_runs old ON old.id=s.run_id
                       JOIN qbwc_invoice_runs current ON current.job_id=old.job_id
                       WHERE current.id=NEW.run_id AND s.phase='write'))
        BEGIN SELECT RAISE(ABORT,'QBWC write cannot be repeated or used for recovery'); END""")
