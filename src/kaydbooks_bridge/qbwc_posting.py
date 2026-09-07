"""Bounded sample invoice posting over QBWC; writes are handed out at most once."""

import argparse
import json
import os
import secrets
import uuid
from pathlib import Path
from xml.etree.ElementTree import ParseError

from qbwc_kit.qbxml import parse_response

from .config import BridgeError, Config
from .invoice_evidence import require
from .invoice_receipt import (
    add_request,
    append_lookup,
    inventory_specs,
    validate_lookup,
    validate_receipt,
)
from .qbwc import ACTIVE_STATES, DurableQBWCDiscoveryService
from .sample_posting import check_preflight, context_hash, gate, preflight, verify_stock_effect
from .service import audited
from .validation import canonical, digest


def _run_id():
    return str(secrets.randbelow(900_000_000_000) + 100_000_000_000)


def _authority(config, policy, store, db, job, actor, now):
    connector = gate(config, actor, policy, job, now)
    from .dispatch import require as dispatch
    from .source_review import require as review

    review(config, policy, store, db, job)
    dispatch(config, actor, policy, store, db, job, now)
    require(config, policy, store, db, job, now)
    if db.execute("SELECT paused FROM control").fetchone()[0]:
        raise BridgeError("company paused")
    if not store.verify_audit(db):
        raise BridgeError("invalid audit")
    return connector


def _idle(db):
    if (
        db.execute(
            "SELECT 1 FROM qbwc_sessions WHERE state IN ('authenticated','request-sent','verified','blocked')"
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
        ).fetchone()
        or db.execute("SELECT 1 FROM master_checks WHERE state='dispatched'").fetchone()
        or db.execute("SELECT 1 FROM qbwc_account_jobs WHERE ticket IS NULL").fetchone()
        or db.execute("SELECT 1 FROM qbwc_invoice_jobs WHERE ticket IS NULL").fetchone()
    ):
        raise BridgeError("finish the existing company read session first")


@audited
def enqueue(bridge, token, company, job_id):
    config, actor, policy, store = bridge._context(token, company, "post-sample")
    now = bridge.clock()
    with store.transaction() as db:
        job = store.job(db, job_id)
        if job["state"] != "queued":
            raise BridgeError("queued invoice required; never resend a dispatched invoice")
        connector = _authority(config, policy, store, db, job, actor, now)
        _idle(db)
        if db.execute(
            "SELECT 1 FROM jobs WHERE state IN ('in-flight','posted-unverified','unknown')"
        ).fetchone():
            raise BridgeError("unresolved company write")
        count = db.execute(
            "SELECT (SELECT COUNT(*) FROM native_invoice_attempts) + (SELECT COUNT(*) FROM qbwc_invoice_attempts)"
        ).fetchone()[0]
        if count >= policy.sample_posting["max_invoices"]:
            raise BridgeError("sample dispatch quota reached")
        run, attempt = _run_id(), uuid.uuid4().hex
        request = add_request(policy, job["payload"], run + "998")
        db.execute(
            "INSERT INTO qbwc_invoice_attempts VALUES (?,?,?,?,?,?,?,?)",
            (
                job_id,
                attempt,
                connector.id,
                actor,
                now,
                request,
                context_hash(policy, job, connector),
                policy.sample_posting["authorization"],
            ),
        )
        db.execute(
            "UPDATE jobs SET state='in-flight',attempt=?,lease_until=?,detail='waiting_for_web_connector' WHERE id=?",
            (attempt, now + 900, job_id),
        )
        db.execute(
            "INSERT INTO qbwc_invoice_runs(id,job_id,recovery,phase,created_at) VALUES(?,?,0,'preflight',?)",
            (run, job_id, now),
        )
        store.event(
            db,
            now,
            actor,
            job_id,
            "qbwc_invoice_queued",
            {"run": run, "attempt": attempt, "request_hash": digest(request)},
        )
        return store.job(db, job_id)


@audited
def recover(bridge, token, company, job_id):
    config, actor, policy, store = bridge._context(token, company, "recover")
    config.authorize(actor, company, "read")
    config.authorize(actor, company, "validate")
    with store.transaction() as db:
        job = store.job(db, job_id)
        attempt = db.execute(
            "SELECT * FROM qbwc_invoice_attempts WHERE job_id=?", (job_id,)
        ).fetchone()
        if (
            attempt is None
            or attempt["actor"] != actor
            or job["state"] not in ("unknown", "posted-unverified")
        ):
            raise BridgeError("owned uncertain QBWC invoice required")
        connector = config.connectors[attempt["connector"]]
        if (
            connector.company != company
            or context_hash(policy, job, connector) != attempt["context_hash"]
            or not store.verify_audit(db)
        ):
            raise BridgeError("original QBWC context and intact audit required")
        _idle(db)
        db.execute(
            "UPDATE qbwc_invoice_runs SET phase='held' WHERE job_id=? AND phase NOT IN ('done','held')",
            (job_id,),
        )
        run = _run_id()
        db.execute(
            "INSERT INTO qbwc_invoice_runs(id,job_id,recovery,phase,created_at) VALUES(?,?,1,'find',?)",
            (run, job_id, bridge.clock()),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            job_id,
            "qbwc_invoice_recovery_queued",
            {"run": run, "read_only": True},
        )
        return store.job(db, job_id)


class DurableQBWCPostingService(DurableQBWCDiscoveryService):
    """Discovery stays unchanged unless a separately authorized invoice was enqueued."""

    @classmethod
    def from_path(cls, path, **kwargs):
        service = super().from_path(path, **kwargs)
        service.config_path = Path(path)
        service.state_root = service.config.root
        return service

    def dispatch(self, body):
        from qbwc_kit import soap

        try:
            self.config = Config.load(self.config_path)
            if self.config.root != self.state_root:
                raise BridgeError("Bridge state directory changed; restart required")
        except (BridgeError, OSError, ValueError):
            return soap.build_fault("Private Bridge configuration unavailable")
        return super().dispatch(body)

    def _do_authenticate(self, call):
        result = super()._do_authenticate(call)
        if not result[0]:
            return result
        ticket = result[0]
        store = self._locate(ticket)
        with store.transaction() as db:
            row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (ticket,)).fetchone()
            pending = db.execute(
                "SELECT r.* FROM qbwc_invoice_runs r JOIN qbwc_invoice_attempts a ON a.job_id=r.job_id WHERE a.connector=? AND r.ticket IS NULL AND r.phase NOT IN ('done','held')",
                (row["connector"],),
            ).fetchone()
            if pending:
                if any(
                    db.execute(f"SELECT 1 FROM {table} WHERE ticket=?", (ticket,)).fetchone()
                    for table in ("qbwc_account_jobs", "qbwc_invoice_jobs")
                ):
                    self._hold(db, store, pending, "conflicting company read job")
                else:
                    db.execute(
                        "UPDATE qbwc_invoice_runs SET ticket=? WHERE id=?", (ticket, pending["id"])
                    )
        return result

    @staticmethod
    def _run(db, ticket):
        return db.execute("SELECT * FROM qbwc_invoice_runs WHERE ticket=?", (ticket,)).fetchone()

    def _hold(self, db, store, run, reason):
        job = store.job(db, run["job_id"])
        if job["state"] == "in-flight":
            db.execute(
                "UPDATE jobs SET state='unknown',detail='qbwc_reconciliation_required' WHERE id=?",
                (job["id"],),
            )
        db.execute("UPDATE qbwc_invoice_runs SET phase='held' WHERE id=?", (run["id"],))
        if run["ticket"]:
            db.execute(
                "UPDATE qbwc_sessions SET last_error=? WHERE ticket=?",
                (reason[:500], run["ticket"]),
            )
        store.event(
            db,
            self.clock(),
            job["submitter"],
            job["id"],
            "qbwc_invoice_held",
            {"run": run["id"], "reason": reason[:500]},
        )

    def _context(self, db, store, run, *, writing=False):
        self.config = Config.load(self.config_path)
        a = db.execute(
            "SELECT * FROM qbwc_invoice_attempts WHERE job_id=?", (run["job_id"],)
        ).fetchone()
        job = store.job(db, run["job_id"])
        actor = a["actor"]
        policy = self.config.authorize(actor, store.company, "read")
        self.config.authorize(actor, store.company, "validate")
        if run["recovery"]:
            self.config.authorize(actor, store.company, "recover")
        connector = self.config.connectors[a["connector"]]
        if (
            connector.company != store.company
            or context_hash(policy, job, connector) != a["context_hash"]
            or job["attempt"] != a["attempt"]
            or not store.verify_audit(db)
        ):
            raise BridgeError("QBWC dispatch context or audit changed")
        if writing:
            _authority(self.config, policy, store, db, job, actor, self.clock())
            if run["recovery"] or job["state"] != "in-flight" or self.clock() >= job["lease_until"]:
                raise BridgeError("QBWC write authority expired or state changed")
        return a, job, policy, connector

    def _do_sendRequestXML(self, call):
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        with store.transaction() as db:
            run = self._run(db, ticket)
            if run:
                session = self._live_row(db, store, ticket, self.clock())
                if session["state"] not in ACTIVE_STATES or run["phase"] in ("done", "held"):
                    return ""
                try:
                    phase = run["phase"]
                    a, job, policy, connector = self._context(
                        db, store, run, writing=phase in ("preflight", "write")
                    )
                    version = self._callback_version(
                        call.get("qbXMLMajorVers"),
                        call.get("qbXMLMinorVers"),
                        call.get("qbXMLCountry"),
                    )
                    if version != "17.0" or call.get("qbXMLCountry") != "US":
                        raise BridgeError("QBWC posting requires US qbXML 17.0")
                    context = canonical(
                        {
                            "company_file": digest(call.get("strCompanyFileName", "")),
                            "country": "US",
                            "version": version,
                        }
                    )
                    hcp = call.get("strHCPResponse")
                    if hcp:
                        self._verify_hcp(hcp, connector)
                    if run["context"] is None:
                        if not hcp:
                            raise BridgeError("initial verified company response required")
                        db.execute(
                            "UPDATE qbwc_invoice_runs SET context=? WHERE id=?",
                            (context, run["id"]),
                        )
                    elif run["context"] != context:
                        raise BridgeError("QBWC session company context changed")
                    old = db.execute(
                        "SELECT * FROM qbwc_invoice_steps WHERE run_id=? AND phase=?",
                        (run["id"], phase),
                    ).fetchone()
                    if old:
                        if phase == "write":
                            raise BridgeError(
                                "write already handed to Web Connector; reconcile without resend"
                            )
                        if digest(old["request"]) != old["request_hash"]:
                            raise BridgeError("QBWC request hash differs")
                        return old["request"]
                    if phase in ("preflight", "find"):
                        request = preflight(policy, job["payload"], run["id"])
                    elif phase == "write":
                        previous = db.execute(
                            "SELECT response FROM qbwc_invoice_responses WHERE run_id=? AND phase='preflight'",
                            (run["id"],),
                        ).fetchone()
                        if (
                            previous is None
                            or check_preflight(
                                previous[0], policy, job["payload"], connector, run["id"]
                            )
                            is not None
                        ):
                            raise BridgeError("fresh collision-free preflight required")
                        first = db.execute(
                            "SELECT sent_at FROM qbwc_invoice_steps WHERE run_id=? AND phase='preflight'",
                            (run["id"],),
                        ).fetchone()
                        if self.clock() - first[0] > 120:
                            raise BridgeError("QBWC preflight expired")
                        request = add_request(policy, job["payload"], run["id"] + "998")
                        if request != a["request"]:
                            raise BridgeError("QBWC approved request differs")
                        if db.execute(
                            "SELECT 1 FROM qbwc_invoice_steps s JOIN qbwc_invoice_runs r ON r.id=s.run_id WHERE r.job_id=? AND s.phase='write'",
                            (job["id"],),
                        ).fetchone():
                            raise BridgeError("invoice write was already handed out")
                        store.event(
                            db,
                            self.clock(),
                            a["actor"],
                            job["id"],
                            "qbwc_invoice_write_handed_out",
                            {"run": run["id"], "request_hash": digest(request)},
                        )
                    elif phase == "lookup":
                        request = append_lookup(
                            self._discovery_request(run["id"], "17.0"),
                            run["id"],
                            run["txn_id"],
                            policy,
                            job["payload"],
                        )
                    else:
                        raise BridgeError("invalid QBWC invoice stage")
                    db.execute(
                        "INSERT INTO qbwc_invoice_steps VALUES(?,?,?,?,?)",
                        (run["id"], phase, request, digest(request), self.clock()),
                    )
                    store.event(
                        db,
                        self.clock(),
                        a["actor"],
                        job["id"],
                        "qbwc_invoice_request",
                        {"run": run["id"], "phase": phase, "request_hash": digest(request)},
                    )
                    return request
                except (BridgeError, KeyError, ValueError, ParseError) as exc:
                    self._hold(db, store, run, str(exc))
                    return ""
        return super()._do_sendRequestXML(call)

    def _do_receiveResponseXML(self, call):
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        response = call.get("response") or call.positional(1)
        callback_hash = digest(
            {"response": response, "hresult": call.get("hresult"), "message": call.get("message")}
        )
        with store.transaction() as db:
            run = self._run(db, ticket)
            if run:
                session = self._live_row(db, store, ticket, self.clock())
                previous = db.execute(
                    "SELECT result FROM qbwc_invoice_responses WHERE run_id=? AND callback_hash=?",
                    (run["id"], callback_hash),
                ).fetchone()
                if previous:
                    return previous[0]
                if run["phase"] in ("done", "held") or session["state"] not in ACTIVE_STATES:
                    return -1
                phase = run["phase"]
                step = db.execute(
                    "SELECT * FROM qbwc_invoice_steps WHERE run_id=? AND phase=?",
                    (run["id"], phase),
                ).fetchone()
                if step is None:
                    self._hold(db, store, run, "response before request")
                    return -1
                result = -1
                try:
                    a, job, policy, connector = self._context(db, store, run)
                    if digest(step["request"]) != step["request_hash"]:
                        raise BridgeError("QBWC request evidence differs")
                    if call.get("hresult"):
                        raise BridgeError("QuickBooks processor error; reconciliation required")
                    if phase in ("preflight", "find"):
                        matched = check_preflight(
                            response,
                            policy,
                            job["payload"],
                            connector,
                            run["id"],
                            recovering=phase == "find",
                        )
                        if matched:
                            if job["txn_id"] and matched["txn_id"] != job["txn_id"]:
                                raise BridgeError("recovery transaction identity differs")
                            db.execute(
                                "UPDATE qbwc_invoice_runs SET phase='lookup',txn_id=? WHERE id=?",
                                (matched["txn_id"], run["id"]),
                            )
                            if job["state"] == "in-flight":
                                db.execute(
                                    "UPDATE jobs SET state='posted-unverified',txn_id=? WHERE id=?",
                                    (matched["txn_id"], job["id"]),
                                )
                            result = 75
                        elif phase == "find":
                            raise BridgeError("invoice absent; no retry authorized")
                        else:
                            db.execute(
                                "UPDATE qbwc_invoice_runs SET phase='write' WHERE id=?",
                                (run["id"],),
                            )
                            result = 25
                    elif phase == "write":
                        receipt = validate_receipt(
                            response,
                            policy,
                            job["payload"],
                            run["id"] + "998",
                            operation="InvoiceAdd",
                        )
                        db.execute(
                            "UPDATE jobs SET state='posted-unverified',txn_id=?,detail='qbwc_readback_pending' WHERE id=?",
                            (receipt["txn_id"], job["id"]),
                        )
                        db.execute(
                            "UPDATE qbwc_invoice_runs SET phase='lookup',txn_id=? WHERE id=?",
                            (receipt["txn_id"], run["id"]),
                        )
                        result = 75
                    elif phase == "lookup":
                        discovery, receipt = validate_lookup(
                            response, run["id"], policy, job["payload"], run["txn_id"]
                        )
                        identity, _ = self._verify_discovery_response(
                            discovery,
                            {"correlation": run["id"], "country": "US", "qbxml_version": "17.0"},
                            connector,
                        )
                        if inventory_specs(policy, job["payload"]):
                            baseline = db.execute(
                                "SELECT p.response FROM qbwc_invoice_responses p JOIN qbwc_invoice_runs r ON r.id=p.run_id JOIN qbwc_invoice_steps s ON s.run_id=r.id AND s.phase='write' WHERE r.job_id=? AND p.phase='preflight'",
                                (job["id"],),
                            ).fetchall()
                            if len(baseline) != 1:
                                raise BridgeError("original inventory sale baseline required")
                            before = {
                                record["ListID"]: record["QuantityOnHand"]
                                for rs in parse_response(baseline[0][0])
                                if rs.entity == "ItemInventory"
                                for record in rs.records
                            }
                            receipt["stock_effects"] = verify_stock_effect(
                                policy, job["payload"], before, receipt["stock_observations"]
                            )
                        proof = {
                            "reference": {
                                "transport": "qbwc-posting",
                                "connector": connector.id,
                                "id": run["id"],
                            },
                            "observed_at": self.clock(),
                            "response_sha256": digest(response),
                            "identity_sha256": identity,
                            "receipt": receipt,
                            "origin": "qbwc-invoice-readback",
                            "bridge_dispatched": bool(
                                db.execute(
                                    "SELECT 1 FROM qbwc_invoice_steps s JOIN qbwc_invoice_runs r ON r.id=s.run_id WHERE r.job_id=? AND s.phase='write'",
                                    (job["id"],),
                                ).fetchone()
                            ),
                        }
                        db.execute(
                            "UPDATE jobs SET state='verified',txn_id=?,detail='qbwc_invoice_verified' WHERE id=?",
                            (receipt["txn_id"], job["id"]),
                        )
                        store.event(
                            db, self.clock(), a["actor"], job["id"], "qbwc_invoice_verified", proof
                        )
                        db.execute(
                            "UPDATE qbwc_invoice_runs SET phase='done' WHERE id=?", (run["id"],)
                        )
                        result = 100
                except (BridgeError, ValueError, KeyError, ParseError) as exc:
                    self._hold(db, store, run, str(exc))
                db.execute(
                    "INSERT INTO qbwc_invoice_responses VALUES(?,?,?,?,?,?)",
                    (run["id"], phase, response, digest(response), callback_hash, result),
                )
                store.event(
                    db,
                    self.clock(),
                    job["submitter"] if "job" in locals() else "qbwc:" + session["connector"],
                    run["job_id"],
                    "qbwc_invoice_response",
                    {
                        "run": run["id"],
                        "phase": phase,
                        "response_hash": digest(response),
                        "result": result,
                    },
                )
                return result
        return super()._do_receiveResponseXML(call)

    def _abandon(self, db, store, ticket, reason):
        run = self._run(db, ticket)
        if run and run["phase"] not in ("done", "held"):
            self._hold(db, store, run, reason)

    def _live_row(self, db, store, ticket, now):
        row = super()._live_row(db, store, ticket, now)
        if row["state"] == "expired":
            self._abandon(db, store, ticket, "Web Connector session expired")
        return row

    def _expire_active(self, db, store, now):
        super()._expire_active(db, store, now)
        for row in db.execute("SELECT ticket FROM qbwc_sessions WHERE state='expired'").fetchall():
            self._abandon(db, store, row["ticket"], "Web Connector session expired")

    def _end(self, call, reason):
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        with store.transaction() as db:
            self._abandon(db, store, ticket, reason)

    def _do_closeConnection(self, call):
        self._end(call, "Web Connector closed before invoice verification")
        return super()._do_closeConnection(call)

    def _do_connectionError(self, call):
        self._end(call, "Web Connector connection failed")
        return super()._do_connectionError(call)


def main(argv=None):
    from .deployment import load_secret_file
    from .service import Bridge

    parser = argparse.ArgumentParser(
        description="Queue bounded QBWC invoice posting or read-only recovery"
    )
    parser.add_argument("action", choices=("enqueue", "recover"))
    for name in ("config", "credentials", "principal", "company", "job"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        load_secret_file(args.credentials)
        config = Config.load(args.config)
        token = os.environ.get(config.principals[args.principal]["token_env"], "")
        result = (enqueue if args.action == "enqueue" else recover)(
            Bridge(args.config), token, args.company, args.job
        )
        print(json.dumps(result, indent=2))
        return 0
    except (BridgeError, OSError, ValueError, KeyError):
        print(json.dumps({"error": "QBWC invoice request rejected; inspect private company audit"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
