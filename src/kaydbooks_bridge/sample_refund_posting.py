"""Explicitly gated sample-company recorded customer refunds. Production posting is unavailable."""

import json
import math
import os
import time
import uuid
from dataclasses import asdict
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, Config
from .customer_refunds import (
    add_request,
    append_check,
    append_query,
    plan,
    validate_check,
    validate_receipt,
    verify_balance_effect,
)
from .direct_sdk import company_lock, discover
from .qbwc import DurableQBWCDiscoveryService
from .refund_evidence import require
from .service import Bridge, audited
from .validation import digest, validate_source


def context_hash(policy, job, connector):
    return digest(
        {
            "policy": plan(policy, job["payload"])["context_sha256"],
            "gate": policy.sample_refund_posting,
            "connector": asdict(connector),
        }
    )


def preflight(policy, payload, run):
    base = append_check(
        DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, plan(policy, payload)
    )
    return append_query(base, run + "999", ref_number=payload["ref_number"])


def check_preflight(response, policy, payload, connector, run, *, recovering=False):
    root = fromstring(response)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid native refund preflight envelope")
    collision = root[0][-1]
    if collision.tag != "ARRefundCreditCardQueryRs" or collision.get("requestID") != run + "999":
        raise BridgeError("uncorrelated refund duplicate query")
    root[0].remove(collision)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=recovering or len(collision) > 0
    )
    DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    if len(collision) == 0 and (collision.get("statusCode"), collision.get("statusSeverity")) in (
        ("1", "Info"),
        ("500", "Warn"),
    ):
        return None, balances
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(collision)
    return validate_receipt(ET.tostring(isolated), policy, payload, run + "999"), balances


def windows_exchange(request, write, folder, approve):
    from .sample_posting import windows_exchange as native

    return native(request, write, folder, approve, helper="native_refund.ps1")


def gate(config, actor, policy, job, now):
    if job["operation"] != "customer-refund.create":
        raise BridgeError("native posting is unavailable for this operation")
    config.authorize(actor, policy.id, "post-sample")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    config.authorize(job["submitter"], policy.id, "submit")
    settings = policy.sample_refund_posting
    if (
        not settings
        or not math.isfinite(settings["expires_at"])
        or now >= settings["expires_at"]
        or not job["payload"]["ref_number"].startswith(settings["ref_prefix"])
    ):
        raise BridgeError("controlled sample authorization is absent, expired or outside scope")
    connector = config.connectors.get(settings["connector"])
    if connector is None or connector.company != policy.id or connector.identity_sha256 == "0" * 64:
        raise BridgeError("confirmed sample connector required")
    if job["submitter"] != actor:
        raise BridgeError("sample posting requires job ownership")
    validate_source(job["source"], policy)
    Bridge._approval(config, policy, job)
    return connector


@audited
def post(bridge, token, company, job_id, *, exchange=windows_exchange, read_exchange=None):
    config, actor, policy, store = bridge._context(token, company, "post-sample")
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            job = store.job(db, job_id)
            connector = gate(config, actor, policy, job, bridge.clock())
            from .source_review import require as require_review

            require_review(config, policy, store, db, job)
            if job["state"] != "queued":
                raise BridgeError(
                    "sample posting requires queued job; never retry a dispatched refund"
                )
            require(config, policy, store, db, job, bridge.clock())
            if not store.verify_audit(db):
                raise BridgeError("invalid audit")
            if db.execute("SELECT paused FROM control").fetchone()[0]:
                raise BridgeError("company paused")
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('in-flight','posted-unverified','unknown')"
            ).fetchone():
                raise BridgeError("unresolved company write")
            if (
                db.execute(
                    "SELECT 1 FROM qbwc_sessions WHERE state IN ('authenticated','request-sent','verified','blocked')"
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
                ).fetchone()
            ):
                raise BridgeError("company read session active")
            if (
                db.execute("SELECT COUNT(*) FROM native_refund_attempts").fetchone()[0]
                >= policy.sample_refund_posting["max_refunds"]
            ):
                raise BridgeError("sample dispatch quota reached")
            attempt = uuid.uuid4().hex
            run = str(int(time.time() * 1000))[-12:]
            request = add_request(policy, job["payload"], run + "998")
            context = context_hash(policy, job, connector)
            db.execute(
                "INSERT INTO native_refund_attempts VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    attempt,
                    connector.id,
                    actor,
                    bridge.clock(),
                    request,
                    context,
                    policy.sample_refund_posting["authorization"],
                ),
            )
            db.execute(
                "UPDATE jobs SET state='in-flight',attempt=?,lease_until=? WHERE id=?",
                (attempt, bridge.clock() + 360, job_id),
            )
            store.event(
                db,
                bridge.clock(),
                actor,
                job_id,
                "refund_dispatch_prepared",
                {"attempt": attempt, "request_hash": digest(request)},
            )
        matched = None

        def approve(response):
            nonlocal matched
            current_config = Config.load(bridge.config_path)
            current_actor = current_config.authenticate(token)
            current_policy = current_config.authorize(current_actor, company, "post-sample")
            with store.transaction() as db:
                current = store.job(db, job_id)
                current_connector = gate(
                    current_config, current_actor, current_policy, current, bridge.clock()
                )
                require_review(current_config, current_policy, store, db, current)
                if (
                    current["state"] != "in-flight"
                    or current["attempt"] != attempt
                    or current["lease_until"] <= bridge.clock()
                    or db.execute("SELECT paused FROM control").fetchone()[0]
                    or context_hash(current_policy, current, current_connector) != context
                    or current_connector != connector
                ):
                    raise BridgeError("dispatch authority or context changed")
                require(current_config, current_policy, store, db, current, bridge.clock())
                matched, balances = check_preflight(
                    response, current_policy, current["payload"], connector, run
                )
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    job_id,
                    "refund_write_authorized" if matched is None else "refund_duplicate_found",
                    {
                        "preflight_hash": digest(response),
                        "refund_before": balances,
                    },
                )
            return matched is None

        folder = store.path.parent / ("native-refund-" + attempt)
        try:
            response = exchange(preflight(policy, job["payload"], run), request, folder, approve)
            if response is not None:
                matched = validate_receipt(
                    response, policy, job["payload"], run + "998", operation="ARRefundCreditCardAdd"
                )
            if matched is None:
                raise BridgeError("native receipt missing")
            bridge._finish(
                store,
                actor,
                job_id,
                attempt,
                "posted-unverified",
                "native_receipt_saved",
                matched["txn_id"],
            )
        except Exception:
            with store.transaction() as db:
                current = store.job(db, job_id)
                if current["state"] == "in-flight":
                    db.execute(
                        "UPDATE jobs SET state='unknown',detail='native_outcome_requires_reconciliation' WHERE id=?",
                        (job_id,),
                    )
                    store.event(
                        db,
                        bridge.clock(),
                        actor,
                        job_id,
                        "native_refund_outcome_unknown",
                        {"attempt": attempt},
                    )
            raise
    # The write helper has closed. Verify via a separate read-only native session.
    return reconcile(bridge, token, company, job_id, exchange=exchange, read_exchange=read_exchange)


@audited
def reconcile(bridge, token, company, job_id, *, exchange=windows_exchange, read_exchange=None):
    config, actor, policy, store = bridge._context(token, company, "recover")
    config.authorize(actor, company, "read")
    config.authorize(actor, company, "validate")
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            job = store.job(db, job_id)
            record = db.execute(
                "SELECT * FROM native_refund_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            if (
                record is None
                or record["actor"] != actor
                or job["state"] not in ("unknown", "posted-unverified")
            ):
                raise BridgeError("owned uncertain native refund required")
            connector = config.connectors[record["connector"]]
            if connector.company != company or not store.verify_audit(db):
                raise BridgeError("native reconciliation binding or audit invalid")
            if context_hash(policy, job, connector) != record["context_hash"]:
                raise BridgeError("native reconciliation requires original dispatch context")
        matched = None
        run = str(int(time.time() * 1000))[-12:]

        def receive(response):
            nonlocal matched
            matched, balances = check_preflight(
                response, policy, job["payload"], connector, run, recovering=True
            )
            return False

        folder = store.path.parent / ("native-refund-reconcile-" + uuid.uuid4().hex)
        exchange(preflight(policy, job["payload"], run), None, folder, receive)
        if matched is None or (job["txn_id"] and matched["txn_id"] != job["txn_id"]):
            raise BridgeError("native reconciliation inconclusive; no retry authorized")
    kwargs = {} if read_exchange is None else {"exchange": read_exchange}
    run_id = str(int(time.time() * 1000))[-12:]
    svc = DurableQBWCDiscoveryService.from_path(bridge.config_path)
    discover(
        svc,
        token,
        connector.id,
        os.environ.get(connector.password_env, ""),
        run_id,
        refund_receipt_check={"txn_id": matched["txn_id"], "payload": job["payload"]},
        **kwargs,
    )
    from .refund_evidence import resolve

    latest, latest_actor, latest_policy, store = bridge._context(token, company, "recover")
    latest.authorize(latest_actor, company, "read")
    latest.authorize(latest_actor, company, "validate")
    with store.transaction() as db:
        current = store.job(db, job_id)
        if (
            current["state"] not in ("unknown", "posted-unverified")
            or current["attempt"] != record["attempt"]
        ):
            raise BridgeError("native reconciliation state changed")
        proof = resolve(
            latest,
            latest_policy,
            store,
            db,
            actor,
            current["payload"],
            {"transport": "direct-sdk", "connector": connector.id, "id": run_id},
            bridge.clock(),
            txn_id=matched["txn_id"],
        )
        if proof["receipt"]["txn_id"] != matched["txn_id"]:
            raise BridgeError("native reconciliation identity changed")
        if (
            context_hash(latest_policy, current, latest.connectors[record["connector"]])
            != record["context_hash"]
        ):
            raise BridgeError("native dispatch context changed during reconciliation")
        add_response = (
            store.path.parent / ("native-refund-" + record["attempt"]) / "add.response.xml"
        )
        dispatched = None
        if add_response.exists():
            correlation = fromstring(record["request"])[0][0].get("requestID")
            validate_receipt(
                add_response.read_text(encoding="utf-8"),
                latest_policy,
                current["payload"],
                correlation,
                operation="ARRefundCreditCardAdd",
                txn_id=matched["txn_id"],
            )
            dispatched = True
        elif db.execute(
            "SELECT 1 FROM audit WHERE job_id=? AND event='refund_duplicate_found'",
            (job_id,),
        ).fetchone():
            dispatched = False
        proof.update(origin="native-attempt-readback", bridge_dispatched=dispatched)
        baseline = db.execute(
            "SELECT data FROM audit WHERE job_id=? AND event='refund_write_authorized' ORDER BY sequence",
            (job_id,),
        ).fetchall()
        if len(baseline) != 1:
            raise BridgeError("refund requires original native balances")
        proof["receipt"]["balance_effects"] = verify_balance_effect(
            current["payload"],
            json.loads(baseline[0]["data"]).get("refund_before"),
            proof["receipt"]["balances"],
        )
        db.execute(
            "UPDATE jobs SET state='verified',txn_id=?,detail='native_refund_verified' WHERE id=?",
            (matched["txn_id"], job_id),
        )
        store.event(db, bridge.clock(), actor, job_id, "native_refund_verified", proof)
        return store.job(db, job_id)
