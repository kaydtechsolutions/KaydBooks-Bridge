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

QBWC_STATES = (
    "authenticated",
    "request-sent",
    "verified",
    "blocked",
    "disconnected",
    "closed",
    "expired",
)


class Store:
    def __init__(self, root: Path, company: str):
        self.company = identifier(company)
        root = outside_repository(root)
        # Windows can canonicalize a missing path differently from the same path
        # once another worker has created it (junctions/short names). Materialize
        # directories first, then compare both existing canonical paths.
        root.mkdir(parents=True, exist_ok=True)
        root = outside_repository(root)
        folder = root / company
        folder.mkdir(parents=True, exist_ok=True)
        folder = folder.resolve()
        if not folder.is_relative_to(root):
            raise BridgeError("company state path escapes private root")
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
            db.execute("""CREATE TABLE IF NOT EXISTS qbwc_account_jobs (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, connector TEXT NOT NULL,
                ticket TEXT UNIQUE)""")
            columns = {r[1] for r in db.execute("PRAGMA table_info(qbwc_account_jobs)")}
            if "list_id" not in columns:
                db.execute("ALTER TABLE qbwc_account_jobs ADD COLUMN list_id TEXT")
            db.execute("""CREATE TRIGGER IF NOT EXISTS account_selector_immutable
                BEFORE UPDATE ON qbwc_account_jobs WHEN NEW.list_id IS NOT OLD.list_id
                BEGIN SELECT RAISE(ABORT,'immutable account selector'); END""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_pending_account_job
                ON qbwc_account_jobs(connector) WHERE ticket IS NULL""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS account_job_immutable
                BEFORE UPDATE ON qbwc_account_jobs WHEN
                NEW.id IS NOT OLD.id OR NEW.actor IS NOT OLD.actor OR
                NEW.connector IS NOT OLD.connector OR
                (OLD.ticket IS NOT NULL AND NEW.ticket IS NOT OLD.ticket)
                BEGIN SELECT RAISE(ABORT,'immutable account job'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS account_job_no_delete
                BEFORE DELETE ON qbwc_account_jobs
                BEGIN SELECT RAISE(ABORT,'durable account job'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS qbwc_invoice_jobs (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, connector TEXT NOT NULL,
                payload TEXT NOT NULL, context_hash TEXT NOT NULL, ticket TEXT UNIQUE)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_pending_invoice_job
                ON qbwc_invoice_jobs(connector) WHERE ticket IS NULL""")
            if "txn_id" not in {r[1] for r in db.execute("PRAGMA table_info(qbwc_invoice_jobs)")}:
                db.execute("ALTER TABLE qbwc_invoice_jobs ADD COLUMN txn_id TEXT")
            db.execute("""CREATE TRIGGER IF NOT EXISTS invoice_selector_immutable
                BEFORE UPDATE OF txn_id ON qbwc_invoice_jobs WHEN NEW.txn_id IS NOT OLD.txn_id
                BEGIN SELECT RAISE(ABORT,'immutable invoice selector'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS invoice_job_immutable
                BEFORE UPDATE ON qbwc_invoice_jobs WHEN NEW.id IS NOT OLD.id OR
                NEW.actor IS NOT OLD.actor OR NEW.connector IS NOT OLD.connector OR
                NEW.payload IS NOT OLD.payload OR NEW.context_hash IS NOT OLD.context_hash OR
                (OLD.ticket IS NOT NULL AND NEW.ticket IS NOT OLD.ticket)
                BEGIN SELECT RAISE(ABORT,'immutable invoice check'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS invoice_job_no_delete
                BEFORE DELETE ON qbwc_invoice_jobs
                BEGIN SELECT RAISE(ABORT,'durable invoice check'); END""")
            for target, other in (
                ("qbwc_invoice_jobs", "qbwc_account_jobs"),
                ("qbwc_account_jobs", "qbwc_invoice_jobs"),
            ):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS {target}_pending_exclusive
                    BEFORE INSERT ON {target} WHEN NEW.ticket IS NULL AND EXISTS
                    (SELECT 1 FROM {other} WHERE connector=NEW.connector AND ticket IS NULL)
                    BEGIN SELECT RAISE(ABORT,'queued read job already exists'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS sdk_discovery (
                id TEXT PRIMARY KEY, connector TEXT NOT NULL, actor TEXT NOT NULL,
                request TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state IN ('prepared','dispatched','verified','blocked')),
                response TEXT, error TEXT NOT NULL DEFAULT '')""")
            sdk_columns = {r[1] for r in db.execute("PRAGMA table_info(sdk_discovery)")}
            if "context_hash" not in sdk_columns:
                db.execute("ALTER TABLE sdk_discovery ADD COLUMN context_hash TEXT")
            db.execute("""CREATE TRIGGER IF NOT EXISTS sdk_context_immutable
                BEFORE UPDATE ON sdk_discovery WHEN NEW.context_hash IS NOT OLD.context_hash
                BEGIN SELECT RAISE(ABORT,'immutable SDK context'); END""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_sdk_discovery
                ON sdk_discovery((1)) WHERE state IN ('prepared','dispatched')""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS sdk_identity_immutable
                BEFORE UPDATE ON sdk_discovery WHEN
                NEW.id IS NOT OLD.id OR NEW.connector IS NOT OLD.connector OR
                NEW.actor IS NOT OLD.actor OR NEW.request IS NOT OLD.request OR
                (OLD.response IS NOT NULL AND NEW.response IS NOT OLD.response) OR
                (OLD.state IN ('verified','blocked') AND NEW.state IS NOT OLD.state)
                BEGIN SELECT RAISE(ABORT,'immutable SDK evidence'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS sdk_no_delete
                BEFORE DELETE ON sdk_discovery
                BEGIN SELECT RAISE(ABORT,'immutable SDK evidence'); END""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS qbwc_sessions (
                ticket TEXT PRIMARY KEY, connector TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN {QBWC_STATES}),
                created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
                correlation TEXT NOT NULL UNIQUE, hcp_xml TEXT, hcp_hash TEXT,
                company_file_hash TEXT, country TEXT, qbxml_version TEXT,
                request_xml TEXT, request_hash TEXT, request_return_count INTEGER NOT NULL DEFAULT 0,
                response_xml TEXT, response_hash TEXT, response_result INTEGER,
                response_callback_count INTEGER NOT NULL DEFAULT 0,
                identity_hash TEXT, host_evidence TEXT, last_error TEXT NOT NULL DEFAULT '',
                close_result TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_qbwc_company
                ON qbwc_sessions ((1))
                WHERE state IN ('authenticated','request-sent','verified','blocked')""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_session_identity_immutable
                BEFORE UPDATE OF ticket,connector,created_at,correlation ON qbwc_sessions
                WHEN OLD.ticket IS NOT NEW.ticket
                  OR OLD.connector IS NOT NEW.connector
                  OR OLD.created_at IS NOT NEW.created_at
                  OR OLD.correlation IS NOT NEW.correlation
                BEGIN SELECT RAISE(ABORT, 'QBWC session identity is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_request_immutable
                BEFORE UPDATE OF request_xml,request_hash,hcp_xml,hcp_hash ON qbwc_sessions
                WHEN (OLD.request_xml IS NOT NULL AND OLD.request_xml IS NOT NEW.request_xml)
                  OR (OLD.request_hash IS NOT NULL AND OLD.request_hash IS NOT NEW.request_hash)
                  OR (OLD.hcp_xml IS NOT NULL AND OLD.hcp_xml IS NOT NEW.hcp_xml)
                  OR (OLD.hcp_hash IS NOT NULL AND OLD.hcp_hash IS NOT NEW.hcp_hash)
                BEGIN SELECT RAISE(ABORT, 'QBWC request evidence is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_request_initial_guard
                BEFORE UPDATE OF request_xml,request_hash ON qbwc_sessions
                WHEN OLD.request_xml IS NULL AND NEW.request_xml IS NOT NULL
                  AND (OLD.state != 'authenticated' OR NEW.state != 'request-sent'
                       OR NEW.request_hash IS NULL)
                BEGIN SELECT RAISE(ABORT, 'invalid QBWC request persistence'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_context_immutable_after_send
                BEFORE UPDATE OF hcp_xml,hcp_hash,company_file_hash,country,qbxml_version
                ON qbwc_sessions
                WHEN OLD.state != 'authenticated'
                  AND (OLD.hcp_xml IS NOT NEW.hcp_xml
                       OR OLD.hcp_hash IS NOT NEW.hcp_hash
                       OR OLD.company_file_hash IS NOT NEW.company_file_hash
                       OR OLD.country IS NOT NEW.country
                       OR OLD.qbxml_version IS NOT NEW.qbxml_version)
                BEGIN SELECT RAISE(ABORT, 'QBWC callback context is immutable after send'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_response_immutable
                BEFORE UPDATE OF response_xml,response_hash,response_result,identity_hash,host_evidence
                ON qbwc_sessions
                WHEN (OLD.response_xml IS NOT NULL AND OLD.response_xml IS NOT NEW.response_xml)
                  OR (OLD.response_hash IS NOT NULL AND OLD.response_hash IS NOT NEW.response_hash)
                  OR (OLD.response_result IS NOT NULL
                      AND OLD.response_result IS NOT NEW.response_result)
                  OR (OLD.identity_hash IS NOT NULL
                      AND OLD.identity_hash IS NOT NEW.identity_hash)
                  OR (OLD.host_evidence IS NOT NULL
                      AND OLD.host_evidence IS NOT NEW.host_evidence)
                BEGIN SELECT RAISE(ABORT, 'QBWC response evidence is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_response_initial_guard
                BEFORE UPDATE OF response_xml,response_hash,response_result,identity_hash,host_evidence
                ON qbwc_sessions
                WHEN OLD.response_hash IS NULL AND NEW.response_hash IS NOT NULL
                  AND (OLD.state != 'request-sent'
                       OR NEW.state NOT IN ('verified','blocked')
                       OR NEW.response_xml IS NULL OR NEW.response_result IS NULL
                       OR (NEW.state = 'verified'
                           AND (NEW.identity_hash IS NULL OR NEW.host_evidence IS NULL))
                       OR (NEW.state = 'blocked'
                           AND (NEW.identity_hash IS NOT NULL OR NEW.host_evidence IS NOT NULL)))
                BEGIN SELECT RAISE(ABORT, 'invalid QBWC response persistence'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_state_transition_guard
                BEFORE UPDATE OF state ON qbwc_sessions
                WHEN NOT (
                    OLD.state = NEW.state
                    OR (OLD.state = 'authenticated' AND NEW.state IN
                        ('request-sent','blocked','disconnected','closed','expired'))
                    OR (OLD.state = 'request-sent' AND NEW.state IN
                        ('verified','blocked','disconnected','closed','expired'))
                    OR (OLD.state = 'verified' AND NEW.state IN ('blocked','closed','expired'))
                    OR (OLD.state = 'blocked' AND NEW.state IN ('closed','expired'))
                    OR (OLD.state = 'disconnected' AND NEW.state = 'closed')
                )
                BEGIN SELECT RAISE(ABORT, 'invalid QBWC session state transition'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_session_no_delete
                BEFORE DELETE ON qbwc_sessions
                BEGIN SELECT RAISE(ABORT, 'QBWC sessions are durable evidence'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS qbwc_callbacks (
                sequence INTEGER PRIMARY KEY, at REAL NOT NULL, ticket TEXT NOT NULL,
                method TEXT NOT NULL, input_hash TEXT NOT NULL, result_hash TEXT NOT NULL,
                outcome TEXT NOT NULL,
                FOREIGN KEY(ticket) REFERENCES qbwc_sessions(ticket))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_callbacks_no_update
                BEFORE UPDATE ON qbwc_callbacks
                BEGIN SELECT RAISE(ABORT, 'QBWC callbacks are append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS qbwc_callbacks_no_delete
                BEFORE DELETE ON qbwc_callbacks
                BEGIN SELECT RAISE(ABORT, 'QBWC callbacks are append-only'); END""")
            db.execute(f"""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, source_key TEXT NOT NULL UNIQUE,
                business_key TEXT NOT NULL UNIQUE, operation TEXT NOT NULL,
                submitter TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN {STATES}),
                payload TEXT NOT NULL, source TEXT NOT NULL,
                approval_by TEXT, approval_hash TEXT, attempt TEXT, lease_until REAL,
                txn_id TEXT, detail TEXT NOT NULL DEFAULT '')""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_insert_guard
                BEFORE INSERT ON jobs
                WHEN NEW.state != 'draft'
                  OR NEW.approval_by IS NOT NULL
                  OR NEW.approval_hash IS NOT NULL
                  OR NEW.attempt IS NOT NULL
                  OR NEW.lease_until IS NOT NULL
                  OR NEW.txn_id IS NOT NULL
                  OR NEW.detail != ''
                BEGIN SELECT RAISE(ABORT, 'invalid initial job state'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_invoice_attempts (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
                connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_attempt_no_{action.lower()}
                    BEFORE {action} ON native_invoice_attempts
                    BEGIN SELECT RAISE(ABORT,'native dispatch identity is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS native_attempt_insert_guard
                BEFORE INSERT ON native_invoice_attempts WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='queued'
                 AND submitter=NEW.actor AND attempt IS NULL)
                BEGIN SELECT RAISE(ABORT,'native dispatch requires owned queued job'); END""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_write
                ON jobs ((1)) WHERE state IN ('in-flight', 'posted-unverified', 'unknown')""")
            db.execute("""CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id))""")
            db.execute("INSERT OR IGNORE INTO idempotency_keys SELECT idempotency_key,id FROM jobs")
            db.execute("""CREATE TABLE IF NOT EXISTS bill_policy_bindings (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), context TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_payment_attempts (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
                connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_payment_attempt_no_{action.lower()}
                    BEFORE {action} ON native_payment_attempts
                    BEGIN SELECT RAISE(ABORT,'native payment dispatch identity is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS native_payment_attempt_insert_guard
                BEFORE INSERT ON native_payment_attempts WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='customer-payment.create' AND state='queued'
                 AND submitter=NEW.actor AND attempt IS NULL)
                BEGIN SELECT RAISE(ABORT,'native payment dispatch requires owned queued payment'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS payment_evidence_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), evidence TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS payment_evidence_no_{action.lower()}
                    BEFORE {action} ON payment_evidence_links
                    BEGIN SELECT RAISE(ABORT,'payment evidence is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS payment_evidence_insert_guard
                BEFORE INSERT ON payment_evidence_links WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='draft' AND operation='customer-payment.create')
                BEGIN SELECT RAISE(ABORT,'payment evidence requires draft payment'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_supplier_payment_attempts (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
                connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_supplier_payment_attempt_no_{action.lower()}
                    BEFORE {action} ON native_supplier_payment_attempts
                    BEGIN SELECT RAISE(ABORT,'native payment dispatch identity is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS native_supplier_payment_attempt_insert_guard
                BEFORE INSERT ON native_supplier_payment_attempts WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='supplier-payment.create' AND state='queued'
                 AND submitter=NEW.actor AND attempt IS NULL)
                BEGIN SELECT RAISE(ABORT,'native payment dispatch requires owned queued payment'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS supplier_payment_evidence_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), evidence TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS supplier_payment_evidence_no_{action.lower()}
                    BEFORE {action} ON supplier_payment_evidence_links
                    BEGIN SELECT RAISE(ABORT,'payment evidence is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS supplier_payment_evidence_insert_guard
                BEFORE INSERT ON supplier_payment_evidence_links WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='draft' AND operation='supplier-payment.create')
                BEGIN SELECT RAISE(ABORT,'payment evidence requires draft payment'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS native_bill_attempts (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt TEXT NOT NULL UNIQUE,
                connector TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,
                request TEXT NOT NULL, context_hash TEXT NOT NULL, authorization TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_bill_attempt_no_{action.lower()}
                    BEFORE {action} ON native_bill_attempts
                    BEGIN SELECT RAISE(ABORT,'native bill dispatch identity is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS native_bill_attempt_insert_guard
                BEFORE INSERT ON native_bill_attempts WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='bill.create' AND state='queued'
                 AND submitter=NEW.actor AND attempt IS NULL)
                BEGIN SELECT RAISE(ABORT,'native bill dispatch requires owned queued bill'); END""")
            db.execute("""CREATE TABLE IF NOT EXISTS bill_evidence_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), evidence TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS bill_evidence_no_{action.lower()}
                    BEFORE {action} ON bill_evidence_links
                    BEGIN SELECT RAISE(ABORT,'bill evidence is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS bill_evidence_insert_guard
                BEFORE INSERT ON bill_evidence_links WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='draft' AND operation='bill.create')
                BEGIN SELECT RAISE(ABORT,'bill evidence requires draft bill'); END""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS bill_binding_no_{action.lower()}
                    BEFORE {action} ON bill_policy_bindings
                    BEGIN SELECT RAISE(ABORT,'bill policy binding is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS bill_binding_insert_guard
                BEFORE INSERT ON bill_policy_bindings WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='draft' AND operation='bill.create')
                BEGIN SELECT RAISE(ABORT,'bill binding requires draft bill'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS bill_binding_state_guard
                BEFORE UPDATE OF state ON jobs WHEN NEW.operation='bill.create' AND NOT EXISTS
                (SELECT 1 FROM bill_policy_bindings WHERE job_id=NEW.id)
                BEGIN SELECT RAISE(ABORT,'bill policy binding required'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS invoice_attempt_operation_guard
                BEFORE INSERT ON native_invoice_attempts WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='invoice.create')
                BEGIN SELECT RAISE(ABORT,'native invoice attempt requires invoice'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_identity_immutable
                BEFORE UPDATE OF id,idempotency_key,fingerprint,source_key,business_key,
                operation,submitter,payload,source ON jobs
                WHEN OLD.id IS NOT NEW.id
                  OR OLD.idempotency_key IS NOT NEW.idempotency_key
                  OR OLD.fingerprint IS NOT NEW.fingerprint
                  OR OLD.source_key IS NOT NEW.source_key
                  OR OLD.business_key IS NOT NEW.business_key
                  OR OLD.operation IS NOT NEW.operation
                  OR OLD.submitter IS NOT NEW.submitter
                  OR OLD.payload IS NOT NEW.payload
                  OR OLD.source IS NOT NEW.source
                BEGIN SELECT RAISE(ABORT, 'job identity is immutable'); END""")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_evidence_links'"
            ).fetchone():
                db.execute("DROP TRIGGER IF EXISTS jobs_state_transition_guard")
                db.execute("DROP TRIGGER IF EXISTS jobs_approval_guard")
            db.execute("""CREATE TABLE IF NOT EXISTS invoice_evidence_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id), evidence TEXT NOT NULL)""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS evidence_no_{operation.lower()}
                    BEFORE {operation} ON invoice_evidence_links
                    BEGIN SELECT RAISE(ABORT,'invoice evidence is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS evidence_draft_only
                BEFORE INSERT ON invoice_evidence_links WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND state='draft')
                BEGIN SELECT RAISE(ABORT,'evidence requires draft'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS invoice_evidence_operation_guard
                BEFORE INSERT ON invoice_evidence_links WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='invoice.create')
                BEGIN SELECT RAISE(ABORT,'invoice evidence requires invoice'); END""")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_receipts'"
            ).fetchone():
                db.execute("DROP TRIGGER IF EXISTS jobs_state_transition_guard")
            receipt_schema = """CREATE TABLE IF NOT EXISTS {table} (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), txn_id TEXT NOT NULL UNIQUE,
                actor TEXT NOT NULL, connector TEXT NOT NULL,
                run_id TEXT NOT NULL, context_hash TEXT NOT NULL, evidence TEXT NOT NULL,
                transport TEXT NOT NULL CHECK(transport IN ('direct-sdk','qbwc')))"""
            columns = {r[1] for r in db.execute("PRAGMA table_info(invoice_receipts)")}
            if columns and "transport" not in columns:
                # Atomic upgrade: retain every immutable receipt and restore the guards below.
                for trigger in (
                    "receipt_no_update",
                    "receipt_no_delete",
                    "receipt_insert_guard",
                    "jobs_state_transition_guard",
                    "external_receipt_terminal_guard",
                ):
                    db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                db.execute(receipt_schema.format(table="invoice_receipts_upgrade"))
                db.execute(
                    "INSERT INTO invoice_receipts_upgrade SELECT *, 'direct-sdk' FROM invoice_receipts"
                )
                db.execute("DROP TABLE invoice_receipts")
                db.execute("ALTER TABLE invoice_receipts_upgrade RENAME TO invoice_receipts")
            db.execute(receipt_schema.format(table="invoice_receipts"))
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS receipt_no_{operation.lower()}
                    BEFORE {operation} ON invoice_receipts
                    BEGIN SELECT RAISE(ABORT,'invoice receipts are immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS receipt_insert_guard
                BEFORE INSERT ON invoice_receipts WHEN NOT EXISTS (
                    SELECT 1 FROM jobs j
                    WHERE j.id=NEW.job_id AND j.submitter=NEW.actor
                      AND j.operation='invoice.create' AND j.state IN ('validated','queued')
                      AND j.attempt IS NULL AND j.txn_id IS NULL
                      AND ((NEW.transport='direct-sdk' AND EXISTS
                      (SELECT 1 FROM sdk_discovery s WHERE s.id=NEW.run_id
                      AND s.state='verified' AND s.response IS NOT NULL AND s.error=''
                      AND s.actor=NEW.actor AND s.connector=NEW.connector
                      AND s.context_hash=NEW.context_hash))
                      OR (NEW.transport='qbwc' AND EXISTS
                      (SELECT 1 FROM qbwc_invoice_jobs q JOIN qbwc_sessions s ON s.ticket=q.ticket
                       WHERE q.id=NEW.run_id AND q.actor=NEW.actor AND q.connector=NEW.connector
                       AND q.context_hash=NEW.context_hash AND q.txn_id=NEW.txn_id
                       AND s.connector=NEW.connector AND s.state IN ('verified','closed')
                       AND s.response_result=100 AND s.last_error=''))))
                BEGIN SELECT RAISE(ABORT,'receipt requires owned undispatched job and verified evidence'); END""")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_bill_rejections'"
            ).fetchone():
                db.execute("DROP TRIGGER IF EXISTS jobs_state_transition_guard")
            db.execute("""CREATE TABLE IF NOT EXISTS native_bill_rejections (
                job_id TEXT PRIMARY KEY REFERENCES native_bill_attempts(job_id), evidence TEXT NOT NULL)""")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS native_bill_rejection_no_{action.lower()}
                    BEFORE {action} ON native_bill_rejections
                    BEGIN SELECT RAISE(ABORT,'bill rejection evidence is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS native_bill_rejection_insert_guard
                BEFORE INSERT ON native_bill_rejections WHEN NOT EXISTS
                (SELECT 1 FROM jobs WHERE id=NEW.job_id AND operation='bill.create' AND state='unknown' AND txn_id IS NULL)
                BEGIN SELECT RAISE(ABORT,'rejection requires an uncertain native bill without receipt'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_state_transition_guard
                BEFORE UPDATE OF state ON jobs
                WHEN NOT (
                    OLD.state = NEW.state
                    OR (OLD.state = 'draft' AND NEW.state = 'validated')
                    OR (OLD.state = 'validated' AND NEW.state = 'queued')
                    OR (OLD.state IN ('validated','queued') AND NEW.state='verified'
                        AND OLD.attempt IS NULL AND EXISTS
                        (SELECT 1 FROM invoice_receipts WHERE job_id=OLD.id AND txn_id=NEW.txn_id))
                    OR (OLD.state IN ('validated','queued') AND NEW.state = 'draft'
                        AND OLD.attempt IS NULL AND NEW.approval_by IS NULL
                        AND NEW.approval_hash IS NULL)
                    OR (OLD.state = 'queued' AND NEW.state = 'in-flight')
                    OR (OLD.state = 'in-flight' AND NEW.state IN
                        ('posted-unverified', 'blocked', 'failed', 'unknown'))
                    OR (OLD.state IN ('posted-unverified', 'unknown')
                        AND NEW.state = 'verified')
                    OR (OLD.state='unknown' AND NEW.state='failed' AND NEW.operation='bill.create'
                        AND EXISTS (SELECT 1 FROM native_bill_rejections WHERE job_id=OLD.id))
                )
                BEGIN SELECT RAISE(ABORT, 'invalid job state transition'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_approval_guard
                BEFORE UPDATE OF approval_by,approval_hash ON jobs
                WHEN (OLD.approval_by IS NOT NEW.approval_by
                  OR OLD.approval_hash IS NOT NEW.approval_hash)
                  AND NOT (OLD.state IN ('validated','queued') AND NEW.state='draft'
                    AND OLD.attempt IS NULL AND NEW.approval_by IS NULL
                    AND NEW.approval_hash IS NULL)
                BEGIN
                    SELECT CASE
                        WHEN OLD.state != 'validated'
                          OR OLD.approval_by IS NOT NULL
                          OR OLD.approval_hash IS NOT NULL
                          OR NEW.approval_by IS NULL
                          OR NEW.approval_hash != OLD.fingerprint
                        THEN RAISE(ABORT, 'invalid approval mutation')
                    END;
                END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_attempt_guard
                BEFORE UPDATE OF attempt,lease_until ON jobs
                WHEN OLD.attempt IS NOT NEW.attempt OR OLD.lease_until IS NOT NEW.lease_until
                BEGIN
                    SELECT CASE
                        WHEN OLD.state != 'queued'
                          OR NEW.state != 'in-flight'
                          OR OLD.attempt IS NOT NULL
                          OR OLD.lease_until IS NOT NULL
                          OR NEW.attempt IS NULL
                          OR NEW.lease_until IS NULL
                        THEN RAISE(ABORT, 'invalid dispatch attempt mutation')
                    END;
                END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_receipt_immutable
                BEFORE UPDATE OF txn_id ON jobs
                WHEN OLD.txn_id IS NOT NEW.txn_id
                  AND (OLD.txn_id IS NOT NULL
                       OR NEW.txn_id IS NULL
                       OR NEW.state NOT IN ('posted-unverified', 'verified'))
                BEGIN SELECT RAISE(ABORT, 'invalid transaction receipt mutation'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS external_receipt_terminal_guard
                BEFORE UPDATE ON jobs WHEN EXISTS
                (SELECT 1 FROM invoice_receipts WHERE job_id=OLD.id
                  AND (NEW.state!='verified' OR txn_id IS NOT NEW.txn_id))
                BEGIN SELECT RAISE(ABORT, 'saved external receipt requires terminal verified job'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_state_invariant_guard
                BEFORE UPDATE ON jobs
                WHEN (OLD.state = 'queued' AND NEW.state = 'in-flight'
                      AND (NEW.attempt IS NULL OR NEW.lease_until IS NULL))
                  OR (((OLD.state = 'in-flight' AND NEW.state = 'posted-unverified')
                       OR (OLD.state IN ('posted-unverified', 'unknown')
                           AND NEW.state = 'verified'))
                      AND NEW.txn_id IS NULL)
                BEGIN SELECT RAISE(ABORT, 'job state invariant violated'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS jobs_no_delete BEFORE DELETE ON jobs
                BEGIN SELECT RAISE(ABORT, 'jobs are append-only records'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS idempotency_no_update
                BEFORE UPDATE ON idempotency_keys
                BEGIN SELECT RAISE(ABORT, 'idempotency keys are append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS idempotency_no_delete
                BEFORE DELETE ON idempotency_keys
                BEGIN SELECT RAISE(ABORT, 'idempotency keys are append-only'); END""")
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
            db.execute("""CREATE TRIGGER IF NOT EXISTS control_boolean_update
                BEFORE UPDATE OF paused ON control WHEN NEW.paused NOT IN (0, 1)
                BEGIN SELECT RAISE(ABORT, 'pause control must be boolean'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS control_no_delete BEFORE DELETE ON control
                BEGIN SELECT RAISE(ABORT, 'pause control is durable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS metadata_no_update BEFORE UPDATE ON metadata
                BEGIN SELECT RAISE(ABORT, 'metadata is immutable'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS metadata_no_delete BEFORE DELETE ON metadata
                BEGIN SELECT RAISE(ABORT, 'metadata is immutable'); END""")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
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
        binding = db.execute(
            "SELECT context FROM bill_policy_bindings WHERE job_id=?", (job_id,)
        ).fetchone()
        if binding:
            result["bill_context"] = json.loads(binding[0])
        evidence_table = (
            "supplier_payment_evidence_links"
            if result["operation"] == "supplier-payment.create"
            else "payment_evidence_links"
            if result["operation"] == "customer-payment.create"
            else "bill_evidence_links"
            if result["operation"] == "bill.create"
            else "invoice_evidence_links"
        )
        evidence = db.execute(
            f"SELECT evidence FROM {evidence_table} WHERE job_id=? ORDER BY sequence DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if evidence:
            result["master_evidence"] = json.loads(evidence[0])
        receipt = db.execute(
            "SELECT evidence FROM invoice_receipts WHERE job_id=?", (job_id,)
        ).fetchone()
        if receipt:
            result["transaction_receipt"] = json.loads(receipt[0])
        elif result["state"] == "verified":
            event = (
                "native_supplier_payment_verified"
                if result["operation"] == "supplier-payment.create"
                else "native_payment_verified"
                if result["operation"] == "customer-payment.create"
                else "native_bill_verified"
                if result["operation"] == "bill.create"
                else "native_invoice_verified"
            )
            native = db.execute(
                "SELECT data FROM audit WHERE job_id=? AND event=? ORDER BY sequence DESC LIMIT 1",
                (job_id, event),
            ).fetchone()
            if native:
                result["transaction_receipt"] = json.loads(native[0])
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
