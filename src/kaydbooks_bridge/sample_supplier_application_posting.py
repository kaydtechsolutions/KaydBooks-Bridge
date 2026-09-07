"""Explicitly gated sample-company supplier credit applications. Production posting is unavailable."""

import json
import math
import os
import time
import uuid
from dataclasses import asdict

from qbwc_kit._xml import fromstring

from .config import BridgeError, Config
from .direct_sdk import company_lock, discover
from .qbwc import DurableQBWCDiscoveryService
from .service import Bridge, audited
from .supplier_application import (
    add_request,
    append_check,
    plan,
    validate_check,
    validate_receipt,
)
from .supplier_application import (
    verify_effect as verify_balance_effect,
)
from .supplier_application_evidence import require
from .validation import digest, validate_source


def context_hash(policy, job, connector):
    return digest(
        {
            "policy": plan(policy, job["payload"])["context_sha256"],
            "gate": policy.sample_supplier_application_posting,
            "connector": asdict(connector),
        }
    )


def preflight(policy, payload, run):
    return append_check(
        DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, plan(policy, payload)
    )


def check_preflight(response, policy, payload, connector, run, *, recovering=False):
    discovery, balances = validate_check(
        response, run, plan(policy, payload), recovering=recovering
    )
    DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    return ({"txn_id": payload["bill_txn_id"]} if recovering else None), balances


def windows_exchange(request, write, folder, approve):
    from .sample_posting import windows_exchange as native

    return native(request, write, folder, approve, helper="native_supplier_application.ps1")


def gate(config, actor, policy, job, now):
    if job["operation"] != "supplier-credit.apply":
        raise BridgeError("native posting is unavailable for this operation")
    config.authorize(actor, policy.id, "post-sample")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    config.authorize(job["submitter"], policy.id, "submit")
    settings = policy.sample_supplier_application_posting
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
            from .dispatch import require as require_dispatch

            require_dispatch(config, actor, policy, store, db, job, bridge.clock())
            if job["state"] != "queued":
                raise BridgeError(
                    "sample posting requires queued job; never retry a dispatched credit"
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
                or db.execute("SELECT 1 FROM master_checks WHERE state='dispatched'").fetchone()
            ):
                raise BridgeError("company read session active")
            if (
                db.execute("SELECT COUNT(*) FROM native_supplier_application_attempts").fetchone()[
                    0
                ]
                >= policy.sample_supplier_application_posting["max_applications"]
            ):
                raise BridgeError("sample dispatch quota reached")
            attempt = uuid.uuid4().hex
            run = str(int(time.time() * 1000))[-12:]
            request = add_request(policy, job["payload"], run + "998")
            context = context_hash(policy, job, connector)
            db.execute(
                "INSERT INTO native_supplier_application_attempts VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    attempt,
                    connector.id,
                    actor,
                    bridge.clock(),
                    request,
                    context,
                    policy.sample_supplier_application_posting["authorization"],
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
                "supplier_application_dispatch_prepared",
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
                require_dispatch(
                    current_config,
                    current_actor,
                    current_policy,
                    store,
                    db,
                    current,
                    bridge.clock(),
                )
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
                    "supplier_application_write_authorized"
                    if matched is None
                    else "supplier_application_duplicate_found",
                    {
                        "preflight_hash": digest(response),
                        "credit_before": balances,
                    },
                )
            return matched is None

        folder = store.path.parent / ("native-supplier-application-" + attempt)
        try:
            response = exchange(preflight(policy, job["payload"], run), request, folder, approve)
            if response is not None:
                matched = validate_receipt(
                    response, policy, job["payload"], run + "998", operation="BillPaymentCheckAdd"
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
                        "native_supplier_application_outcome_unknown",
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
                "SELECT * FROM native_supplier_application_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            if (
                record is None
                or record["actor"] != actor
                or job["state"] not in ("unknown", "posted-unverified")
            ):
                raise BridgeError("owned uncertain native credit required")
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

        folder = store.path.parent / ("native-supplier-application-reconcile-" + uuid.uuid4().hex)
        exchange(preflight(policy, job["payload"], run), None, folder, receive)
        acknowledgement = (
            store.path.parent
            / ("native-supplier-application-" + record["attempt"])
            / "add.response.xml"
        )
        if acknowledgement.exists():
            matched = validate_receipt(
                acknowledgement.read_text(encoding="utf-8"),
                policy,
                job["payload"],
                fromstring(record["request"])[0][0].get("requestID"),
            )
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
        supplier_application_receipt_check={"txn_id": matched["txn_id"], "payload": job["payload"]},
        **kwargs,
    )
    from .supplier_application_evidence import resolve

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
            store.path.parent
            / ("native-supplier-application-" + record["attempt"])
            / "add.response.xml"
        )
        dispatched = None
        if add_response.exists():
            correlation = fromstring(record["request"])[0][0].get("requestID")
            validate_receipt(
                add_response.read_text(encoding="utf-8"),
                latest_policy,
                current["payload"],
                correlation,
                operation="BillPaymentCheckAdd",
                txn_id=matched["txn_id"],
            )
            dispatched = True
        elif db.execute(
            "SELECT 1 FROM audit WHERE job_id=? AND event='supplier_application_duplicate_found'",
            (job_id,),
        ).fetchone():
            dispatched = False
        proof.update(origin="native-attempt-readback", bridge_dispatched=dispatched)
        baseline = db.execute(
            "SELECT data FROM audit WHERE job_id=? AND event='supplier_application_write_authorized' ORDER BY sequence",
            (job_id,),
        ).fetchall()
        if len(baseline) != 1:
            raise BridgeError("credit requires original native customer balance")
        proof["receipt"]["balance_effects"] = verify_balance_effect(
            current["payload"],
            json.loads(baseline[0]["data"]).get("credit_before"),
            proof["receipt"]["balances"],
        )
        db.execute(
            "UPDATE jobs SET state='verified',txn_id=?,detail='native_supplier_application_verified' WHERE id=?",
            (matched["txn_id"], job_id),
        )
        store.event(
            db, bridge.clock(), actor, job_id, "native_supplier_application_verified", proof
        )
        return store.job(db, job_id)
