"""Single company-scoped execution contract for trusted interface adapters."""

from __future__ import annotations

import json
import time
import uuid
from functools import wraps
from pathlib import Path

from .config import BridgeError, Config, identifier, strict_keys
from .invoice_evidence import require as require_invoice_evidence
from .invoice_evidence import resolve as resolve_evidence
from .simulation import SyntheticLedger
from .store import Store
from .validation import canonical, digest, validate_invoice, validate_source

SURFACES = frozenset(
    {"cli", "chat", "documents", "tools", "schedule", "delegation", "kanban", "browser", "desktop"}
)


def validate_payload(operation, payload, policy):
    if operation == "supplier-payment.create":
        from .supplier_payments import validate_payload as validate_payment

        return validate_payment(payload, policy)
    if operation == "customer-payment.create":
        from .customer_payments import validate_payload as validate_payment

        return validate_payment(payload, policy)
    if operation == "bill.create":
        from .bills import validate_payload as validate_bill

        return validate_bill(payload, policy)
    if operation != "invoice.create":
        raise BridgeError("operation unavailable")
    return validate_invoice(payload, policy)


def require_evidence(config, policy, store, db, job, now):
    if job["operation"] == "supplier-payment.create":
        from .supplier_payment_evidence import require

        return require(config, policy, store, db, job, now)
    if job["operation"] == "customer-payment.create":
        from .payment_evidence import require

        return require(config, policy, store, db, job, now)
    if job["operation"] == "bill.create":
        from .bills import require_context

        return require_context(config, policy, store, db, job, now)
    return require_invoice_evidence(config, policy, store, db, job, now)


def audited(method):
    """Record rejected authenticated requests without persisting untrusted content."""

    @wraps(method)
    def call(self, token, company, *args, **kwargs):
        try:
            return method(self, token, company, *args, **kwargs)
        except BridgeError:
            config = Config.load(self.config_path)
            try:
                actor = config.authenticate(token)
            except BridgeError:
                # No company may be attributed to an unauthenticated caller.
                raise BridgeError("authentication failed") from None
            if company in config.companies:
                store = Store(config.root, company)
                with store.transaction() as db:
                    store.event(
                        db,
                        self.clock(),
                        actor,
                        None,
                        "request_rejected",
                        {"action": method.__name__},
                    )
            raise

    return call


class Bridge:
    def __init__(self, config_path: str | Path, *, clock=time.time):
        self.config_path = config_path
        self.clock = clock

    def _context(self, token, company, permission):
        config = Config.load(self.config_path)  # re-read policy and environment credentials
        actor = config.authenticate(token)
        selected = config.authorize(actor, company, permission)
        return config, actor, selected, Store(config.root, company)

    @audited
    def prepare(self, token: str, company: str, envelope: dict) -> dict:
        config, actor, policy, store = self._context(token, company, "prepare")
        strict_keys(
            envelope,
            {"operation", "idempotency_key", "surface", "payload", "source"},
            {"master_evidence"},
        )
        if envelope["operation"] not in (
            "invoice.create",
            "bill.create",
            "customer-payment.create",
            "supplier-payment.create",
        ):
            raise BridgeError("operation unavailable")
        if envelope["surface"] not in SURFACES:
            raise BridgeError("unsupported interface")
        identifier(envelope["idempotency_key"])
        payload = validate_payload(envelope["operation"], envelope["payload"], policy)
        source = validate_source(envelope["source"], policy)
        fingerprint = digest(
            {"operation": envelope["operation"], "payload": payload, "source": source}
        )
        source_key = canonical([source["namespace"], source["reference"]])
        business_key = canonical([envelope["operation"], payload["ref_number"].casefold()])
        bill_context = None
        if envelope["operation"] == "bill.create":
            from .bills import context

            bill_context = context(policy, payload)
            business_key = canonical(
                ["bill.create", bill_context["vendor_list_id"], payload["ref_number"].casefold()]
            )
        with store.transaction() as db:
            matches = db.execute(
                "SELECT id,fingerprint FROM jobs WHERE id IN (SELECT job_id FROM idempotency_keys WHERE key=?) OR source_key=? OR business_key=?",
                (envelope["idempotency_key"], source_key, business_key),
            ).fetchall()
            if matches:
                if len(matches) != 1 or matches[0]["fingerprint"] != fingerprint:
                    raise BridgeError(
                        "duplicate key, source or reference conflicts with existing job"
                    )
                existing = store.job(db, matches[0]["id"])
                if existing.get("transaction_receipt"):
                    config.authorize(actor, company, "read")
                    if existing["submitter"] != actor or existing["state"] != "verified":
                        raise BridgeError("receipt requires an owned verified job")
                    if not store.verify_audit(db):
                        raise BridgeError("receipt evidence audit is invalid")
                    db.execute(
                        "INSERT OR IGNORE INTO idempotency_keys VALUES (?,?)",
                        (envelope["idempotency_key"], existing["id"]),
                    )
                    store.event(
                        db,
                        self.clock(),
                        actor,
                        existing["id"],
                        "duplicate_prevented",
                        {"surface": envelope["surface"], "receipt_retained": True},
                    )
                    return existing
            evidence = None
            if "master_evidence" in envelope:
                resolver = resolve_evidence
                if bill_context is not None:
                    from .bill_evidence import resolve as resolver
                if envelope["operation"] == "customer-payment.create":
                    from .payment_evidence import resolve as resolver
                if envelope["operation"] == "supplier-payment.create":
                    from .supplier_payment_evidence import resolve as resolver
                evidence = resolver(
                    config,
                    policy,
                    store,
                    db,
                    actor,
                    payload,
                    envelope["master_evidence"],
                    self.clock(),
                )
            elif envelope["operation"] in (
                "customer-payment.create",
                "supplier-payment.create",
            ) or (policy.invoice_masters and bill_context is None):
                raise BridgeError("verified invoice master evidence required")
            if matches:
                job_id = matches[0]["id"]
                existing = store.job(db, job_id)
                if evidence != existing.get("master_evidence"):
                    if evidence is None:
                        raise BridgeError("linked invoice evidence cannot be removed")
                    if existing["submitter"] != actor or existing["state"] not in (
                        "draft",
                        "validated",
                        "queued",
                    ):
                        raise BridgeError("only the owner may refresh evidence before dispatch")
                    db.execute(
                        "UPDATE jobs SET state='draft',approval_by=NULL,approval_hash=NULL WHERE id=?",
                        (job_id,),
                    )
                    self._link_evidence(store, db, actor, job_id, evidence)
                require_evidence(config, policy, store, db, store.job(db, job_id), self.clock())
                db.execute(
                    "INSERT OR IGNORE INTO idempotency_keys VALUES (?,?)",
                    (envelope["idempotency_key"], job_id),
                )
                store.event(
                    db,
                    self.clock(),
                    actor,
                    job_id,
                    "duplicate_prevented",
                    {"surface": envelope["surface"]},
                )
                return store.job(db, job_id)
            job_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO jobs (id,idempotency_key,fingerprint,source_key,business_key,
                       operation,submitter,state,payload,source) VALUES (?,?,?,?,?,?,?,'draft',?,?)""",
                (
                    job_id,
                    envelope["idempotency_key"],
                    fingerprint,
                    source_key,
                    business_key,
                    envelope["operation"],
                    actor,
                    canonical(payload),
                    canonical(source),
                ),
            )
            db.execute(
                "INSERT INTO idempotency_keys VALUES (?,?)", (envelope["idempotency_key"], job_id)
            )
            if bill_context is not None:
                db.execute(
                    "INSERT INTO bill_policy_bindings VALUES (?,?)",
                    (job_id, canonical(bill_context)),
                )
            if evidence is not None:
                self._link_evidence(store, db, actor, job_id, evidence)
            store.event(
                db,
                self.clock(),
                actor,
                job_id,
                "prepared",
                {"surface": envelope["surface"], "fingerprint": fingerprint},
            )
            return store.job(db, job_id)

    def _link_evidence(self, store, db, actor, job_id, evidence):
        bill = store.job(db, job_id)["operation"] == "bill.create"
        table = "bill_evidence_links" if bill else "invoice_evidence_links"
        payment = store.job(db, job_id)["operation"] == "customer-payment.create"
        supplier_payment = store.job(db, job_id)["operation"] == "supplier-payment.create"
        if payment:
            table = "payment_evidence_links"
        if supplier_payment:
            table = "supplier_payment_evidence_links"
        db.execute(
            f"INSERT INTO {table}(job_id,evidence) VALUES (?,?)",
            (job_id, canonical(evidence)),
        )
        store.event(
            db,
            self.clock(),
            actor,
            job_id,
            "supplier_payment_evidence_linked"
            if supplier_payment
            else "payment_evidence_linked"
            if payment
            else "bill_evidence_linked"
            if bill
            else "invoice_evidence_linked",
            evidence,
        )

    @audited
    def attach_receipt(self, token: str, company: str, job_id: str, reference: dict) -> dict:
        """Mark an already-saved invoice verified; never dispatch a transaction."""
        from .receipt_evidence import resolve

        config, actor, policy, store = self._context(token, company, "recover")
        config.authorize(actor, company, "read")
        config.authorize(actor, company, "validate")
        strict_keys(reference, {"transport", "connector", "id"})
        with store.transaction() as db:
            job = store.job(db, job_id)
            if job["submitter"] != actor:
                raise BridgeError("receipt attachment requires job ownership")
            saved = job.get("transaction_receipt")
            if saved is not None:
                if (
                    saved["reference"] != reference
                    or job["state"] != "verified"
                    or not store.verify_audit(db)
                ):
                    raise BridgeError("existing receipt cannot be replaced")
                return job
            if (
                job["state"] not in ("validated", "queued")
                or job["attempt"] is not None
                or job["operation"] != "invoice.create"
                or job["txn_id"] is not None
            ):
                raise BridgeError("receipt attachment requires an undispatched validated invoice")
            evidence = resolve(
                config, policy, store, db, actor, job["payload"], reference, self.clock()
            )
            txn_id = evidence["receipt"]["txn_id"]
            if db.execute("SELECT 1 FROM invoice_receipts WHERE txn_id=?", (txn_id,)).fetchone():
                raise BridgeError("saved transaction already belongs to another job")
            db.execute(
                "INSERT INTO invoice_receipts VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    txn_id,
                    actor,
                    reference["connector"],
                    reference["id"],
                    evidence["context_sha256"],
                    canonical(evidence),
                    reference["transport"],
                ),
            )
            db.execute(
                "UPDATE jobs SET state='verified',txn_id=?,detail='verified_external_invoice' WHERE id=?",
                (txn_id, job_id),
            )
            store.event(db, self.clock(), actor, job_id, "invoice_receipt_attached", evidence)
            return store.job(db, job_id)

    @audited
    def verify_receipt(self, token: str, company: str, job_id: str, reference: dict) -> dict:
        """Append a fresh readback proof without replacing the original receipt."""
        from .receipt_evidence import resolve

        config, actor, policy, store = self._context(token, company, "read")
        config.authorize(actor, company, "validate")
        with store.transaction() as db:
            job = store.job(db, job_id)
            if (
                job["submitter"] != actor
                or job["state"] != "verified"
                or not job.get("transaction_receipt")
            ):
                raise BridgeError(
                    "fresh receipt verification requires an owned externally verified job"
                )
            if job["operation"] == "bill.create":
                from .bill_receipt_evidence import resolve
            if job["operation"] in ("customer-payment.create", "supplier-payment.create"):
                raise BridgeError("use dedicated native payment reconciliation")
            evidence = resolve(
                config, policy, store, db, actor, job["payload"], reference, self.clock()
            )
            if evidence["receipt"]["txn_id"] != job["txn_id"]:
                raise BridgeError("fresh receipt transaction identity mismatch")
            store.event(
                db,
                self.clock(),
                actor,
                job_id,
                "bill_receipt_confirmed"
                if job["operation"] == "bill.create"
                else "invoice_receipt_confirmed",
                evidence,
            )
            return {
                "job_id": job_id,
                "state": "verified",
                "observation": evidence,
                "live_posting": False,
            }

    @audited
    def preview(self, token: str, company: str, job_id: str) -> dict:
        """Review a validated owned invoice using current verified evidence; no dispatch."""
        from .invoice_preview import build

        config, actor, policy, store = self._context(token, company, "read")
        config.authorize(actor, company, "validate")
        with store.transaction() as db:
            job = store.job(db, job_id)
            if job["submitter"] != actor or job["state"] != "validated":
                raise BridgeError("invoice preview requires an owned validated job")
            require_evidence(config, policy, store, db, job, self.clock())
            if job["operation"] == "bill.create":
                from .bills import preview
                from .source_review import require as require_review

                require_review(config, policy, store, db, job)
                result = preview(policy, job)
                store.event(
                    db,
                    self.clock(),
                    actor,
                    job_id,
                    "bill_previewed",
                    {"preview_sha256": result["preview_sha256"]},
                )
                return result
            if job["operation"] in ("customer-payment.create", "supplier-payment.create"):
                from .source_review import require as require_review

                require_review(config, policy, store, db, job)
                result = {
                    "schema": job["operation"].removesuffix(".create") + "-review-v1",
                    "job": job_id,
                    "company": company,
                    "payload": job["payload"],
                    "balances": job["master_evidence"]["balances"],
                    "total": job["payload"]["total_amount"],
                    "live_posting": False,
                    "posting_authorized_by_preview": False,
                }
                result["preview_sha256"] = digest(result)
                store.event(
                    db,
                    self.clock(),
                    actor,
                    job_id,
                    "payment_previewed",
                    {"preview_sha256": result["preview_sha256"]},
                )
                return result
            if job["operation"] != "invoice.create":
                raise BridgeError("unsupported preview operation")
            result = build(policy, job)
            store.event(
                db,
                self.clock(),
                actor,
                job_id,
                "invoice_previewed",
                {"preview_sha256": result["preview_sha256"]},
            )
            return result

    @audited
    def action(self, token: str, company: str, job_id: str, action: str) -> dict:
        if action not in {"validate", "approve", "submit"}:
            raise BridgeError("unsupported action; job state cannot be set by a client")
        config, actor, policy, store = self._context(token, company, action)
        with store.transaction() as db:
            job = store.job(db, job_id)
            expected = {"validate": "draft", "approve": "validated", "submit": "validated"}[action]
            if job["state"] != expected:
                raise BridgeError("action is invalid for the current job state")
            validate_payload(job["operation"], job["payload"], policy)
            require_evidence(config, policy, store, db, job, self.clock())
            validate_source(job["source"], policy)
            from .source_review import require as require_review

            require_review(config, policy, store, db, job)
            if action == "approve":
                if actor == job["submitter"]:
                    raise BridgeError("approval must come from a different principal")
                db.execute(
                    "UPDATE jobs SET approval_by=?,approval_hash=? WHERE id=?",
                    (actor, job["fingerprint"], job_id),
                )
            else:
                if action == "submit":
                    self._approval(config, policy, job)
                db.execute(
                    "UPDATE jobs SET state=? WHERE id=?",
                    ("validated" if action == "validate" else "queued", job_id),
                )
            store.event(db, self.clock(), actor, job_id, action, {})
            return store.job(db, job_id)

    @staticmethod
    def _approval(config, policy, job):
        if policy.approval_required:
            if not job["approval_by"] or job["approval_hash"] != job["fingerprint"]:
                raise BridgeError("approval required")
            config.authorize(job["approval_by"], policy.id, "approve")

    @audited
    def status(self, token: str, company: str, job_id: str | None = None) -> dict:
        _, actor, _, store = self._context(token, company, "read")
        with store.transaction() as db:
            if job_id:
                result = store.job(db, job_id)
            else:
                result = {
                    "company": company,
                    "mode": "simulation",
                    "live_posting": False,
                    "paused": bool(db.execute("SELECT paused FROM control").fetchone()[0]),
                    "jobs": [
                        dict(row)
                        for row in db.execute(
                            "SELECT id,state,operation,detail,txn_id FROM jobs ORDER BY rowid"
                        )
                    ],
                    "audit_valid": store.verify_audit(db),
                }
            store.event(db, self.clock(), actor, job_id, "read", {})
            return result

    @audited
    def pause(self, token: str, company: str, paused: bool):
        if type(paused) is not bool:
            raise BridgeError("paused must be boolean")
        _, actor, _, store = self._context(token, company, "pause")
        with store.transaction() as db:
            db.execute("UPDATE control SET paused=?", (int(paused),))
            store.event(db, self.clock(), actor, None, "paused" if paused else "resumed", {})
        return {"company": company, "paused": paused}

    @audited
    def simulate(self, token: str, company: str) -> dict | None:
        config, actor, policy, store = self._context(token, company, "simulate")
        ledger = SyntheticLedger(store, policy)  # no injectable production transport
        with store.transaction() as db:
            if db.execute("SELECT paused FROM control").fetchone()[0]:
                raise BridgeError("company is paused")
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('in-flight','posted-unverified','unknown')"
            ).fetchone():
                raise BridgeError("company has an unresolved write; reconcile before dispatch")
            row = db.execute(
                "SELECT id FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job = store.job(db, row["id"])
            if job["operation"] in ("customer-payment.create", "supplier-payment.create"):
                raise BridgeError("payment requires dedicated native dispatch or reconciliation")
            if db.execute(
                "SELECT 1 FROM native_invoice_attempts WHERE job_id=? UNION ALL SELECT 1 FROM native_bill_attempts WHERE job_id=?",
                (job["id"], job["id"]),
            ).fetchone():
                raise BridgeError("native invoice cannot enter the simulator")
            # Initiator and worker both need current authority. Delegation adds none.
            config.authorize(job["submitter"], company, "submit")
            require_evidence(config, policy, store, db, job, self.clock())
            validate_payload(job["operation"], job["payload"], policy)
            validate_source(job["source"], policy)
            self._approval(config, policy, job)
            from .source_review import require as require_review

            require_review(config, policy, store, db, job)
            attempt = uuid.uuid4().hex
            db.execute(
                "UPDATE jobs SET state='in-flight',attempt=?,lease_until=? WHERE id=?",
                (attempt, self.clock() + 60, job["id"]),
            )
            store.event(
                db,
                self.clock(),
                actor,
                job["id"],
                "dispatch_intent",
                {"attempt": attempt, "mode": "simulation"},
            )
        try:
            if ledger.identity() != policy.simulation_identity or not ledger.masters_valid(
                job["payload"]
            ):
                return self._finish(
                    store, actor, job["id"], attempt, "blocked", "identity_or_master_mismatch"
                )
            matches = ledger.find(job["payload"])
            if matches:
                if len(matches) != 1 or matches[0]["payload"] != job["payload"]:
                    return self._finish(
                        store, actor, job["id"], attempt, "blocked", "external_duplicate_conflict"
                    )
                txn_id = matches[0]["txn_id"]
            else:
                # Last check at the write boundary. Hold the local transaction for
                # this local simulator only, preventing recovery/pause overtaking it.
                with store.transaction() as db:
                    current = store.job(db, job["id"])
                    if (
                        current["attempt"] != attempt
                        or current["state"] != "in-flight"
                        or current["lease_until"] <= self.clock()
                    ):
                        raise BridgeError("stale dispatch")
                    if db.execute("SELECT paused FROM control").fetchone()[0]:
                        raise BridgeError("company paused before write")
                    latest = Config.load(self.config_path)
                    latest_actor = latest.authenticate(token)
                    latest_policy = latest.authorize(latest_actor, company, "simulate")
                    latest.authorize(job["submitter"], company, "submit")
                    require_evidence(latest, latest_policy, store, db, job, self.clock())
                    validate_payload(job["operation"], job["payload"], latest_policy)
                    validate_source(job["source"], latest_policy)
                    require_review(latest, latest_policy, store, db, job)
                    self._approval(latest, latest_policy, job)
                    if ledger.identity() != latest_policy.simulation_identity:
                        raise BridgeError("company changed before write")
                    txn_id = ledger.write(job["payload"])
            # Save receipt before a separate read-back. A crash here must not resend.
            self._finish(
                store, actor, job["id"], attempt, "posted-unverified", "receipt_saved", txn_id
            )
            saved = ledger.read(txn_id)
            state = "verified" if saved == job["payload"] else "posted-unverified"
            return self._finish(
                store,
                actor,
                job["id"],
                attempt,
                state,
                "saved_record_matches" if state == "verified" else "saved_record_mismatch",
                txn_id,
                evidence={"saved_record_hash": digest(saved)},
            )
        except Exception:
            # Exception strings may contain source data/credentials. Persist only a safe code.
            with store.transaction() as db:
                current = store.job(db, job["id"])
            state = "posted-unverified" if current["txn_id"] else "unknown"
            return self._finish(
                store,
                actor,
                job["id"],
                attempt,
                state,
                "adapter_outcome_requires_reconciliation",
                current["txn_id"],
            )

    def _finish(self, store, actor, job_id, attempt, state, detail, txn_id=None, evidence=None):
        with store.transaction() as db:
            job = store.job(db, job_id)
            if job["attempt"] != attempt or job["state"] not in {"in-flight", "posted-unverified"}:
                raise BridgeError("stale worker result; reconciliation required")
            db.execute(
                "UPDATE jobs SET state=?,detail=?,txn_id=? WHERE id=?",
                (state, detail, txn_id, job_id),
            )
            store.event(
                db,
                self.clock(),
                actor,
                job_id,
                state,
                {"detail": detail, "txn_id": txn_id, **(evidence or {})},
            )
            return store.job(db, job_id)

    @audited
    def recover(self, token: str, company: str) -> dict:
        _, actor, _, store = self._context(token, company, "recover")
        with store.transaction() as db:
            rows = db.execute(
                "SELECT id FROM jobs WHERE state='in-flight' AND lease_until<=?", (self.clock(),)
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET state='unknown',detail='expired_dispatch' WHERE id=?",
                    (row["id"],),
                )
                store.event(
                    db, self.clock(), actor, row["id"], "unknown", {"detail": "expired_dispatch"}
                )
        return {"recovered_to_unknown": len(rows), "writes_retried": 0}

    @audited
    def reconcile(self, token: str, company: str, job_id: str) -> dict:
        config, actor, policy, store = self._context(token, company, "recover")
        ledger = SyntheticLedger(store, policy)
        # Serialize reconciliation with completion, recovery and competing reconcilers.
        # The simulation read is local. A real asynchronous transport needs persisted
        # read intents and fenced callbacks, not a database transaction over the network.
        with store.transaction() as db:
            job = store.job(db, job_id)
            if job["operation"] in ("customer-payment.create", "supplier-payment.create"):
                raise BridgeError("payment requires dedicated native dispatch or reconciliation")
            if job["state"] not in {"unknown", "posted-unverified"}:
                raise BridgeError("only uncertain outcomes can be reconciled")
            if job["operation"] == "bill.create":
                require_evidence(config, policy, store, db, job, self.clock())
            if db.execute(
                "SELECT 1 FROM native_invoice_attempts WHERE job_id=? UNION ALL SELECT 1 FROM native_bill_attempts WHERE job_id=?",
                (job_id, job_id),
            ).fetchone():
                raise BridgeError("native invoice requires sample reconciliation")
            if ledger.identity() != policy.simulation_identity:
                raise BridgeError("connected company mismatch")
            matches = ledger.find(job["payload"])
            state, detail, txn_id = job["state"], "reconciliation_inconclusive", job["txn_id"]
            evidence = {}
            if len(matches) == 1 and matches[0]["payload"] == job["payload"]:
                candidate = matches[0]["txn_id"]
                saved = ledger.read(candidate)
                if (txn_id is None or txn_id == candidate) and saved == job["payload"]:
                    state, detail, txn_id = "verified", "reconciled_saved_record", candidate
                    evidence = {"saved_record_hash": digest(saved)}
            db.execute(
                "UPDATE jobs SET state=?,detail=?,txn_id=? WHERE id=?",
                (state, detail, txn_id, job_id),
            )
            store.event(
                db,
                self.clock(),
                actor,
                job_id,
                "reconciled",
                {"state": state, "detail": detail, "txn_id": txn_id, **evidence},
            )
            return store.job(db, job_id)

    @audited
    def audit(self, token: str, company: str) -> dict:
        _, actor, _, store = self._context(token, company, "read")
        with store.transaction() as db:
            store.event(db, self.clock(), actor, None, "audit_read", {})
            return {
                "valid": store.verify_audit(db),
                "events": [
                    {**dict(row), "data": json.loads(row["data"])}
                    for row in db.execute("SELECT * FROM audit ORDER BY sequence")
                ],
            }
