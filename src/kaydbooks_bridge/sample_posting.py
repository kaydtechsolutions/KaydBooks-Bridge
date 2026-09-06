"""Explicitly gated sample-company invoices. Production posting is unavailable."""

import hashlib
import math
import os
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, Config
from .direct_sdk import company_lock, discover
from .invoice_compatibility import append_queries, plan, validate_response
from .invoice_evidence import require
from .invoice_receipt import RECEIPT_FIELDS, add_request, validate_receipt
from .qbwc import DurableQBWCDiscoveryService
from .service import Bridge, audited
from .validation import digest, validate_source


def save(path, value):
    """Exclusive, flushed publication; never overwrite evidence or authorization."""
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("x", encoding="utf-8", newline="") as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    os.link(temporary, path)
    temporary.unlink()


def context_hash(policy, job, connector):
    return digest(
        {
            "policy": plan(policy, job["payload"])["context_sha256"],
            "gate": policy.sample_posting,
            "connector": asdict(connector),
        }
    )


def preflight(policy, payload, run):
    check = plan(policy, payload)
    root = fromstring(
        append_queries(DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, check)
    )
    query = ET.SubElement(root[0], "InvoiceQueryRq", requestID=run + "999")
    ET.SubElement(query, "RefNumber").text = payload["ref_number"]
    ET.SubElement(query, "IncludeLineItems").text = "true"
    ET.SubElement(query, "IncludeLinkedTxns").text = "true"
    for field in RECEIPT_FIELDS:
        ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def check_preflight(response, policy, payload, connector, run):
    root = fromstring(response)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid native preflight envelope")
    collision = root[0][-1]
    if collision.tag != "InvoiceQueryRs" or collision.get("requestID") != run + "999":
        raise BridgeError("uncorrelated invoice duplicate query")
    root[0].remove(collision)
    discovery = validate_response(ET.tostring(root), run, plan(policy, payload))
    DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    status = collision.get("statusCode"), collision.get("statusSeverity")
    if len(collision) == 0 and status in (("1", "Info"), ("500", "Warn")):
        return None
    invoice_root = ET.Element("QBXML")
    ET.SubElement(invoice_root, "QBXMLMsgsRs").append(collision)
    return validate_receipt(ET.tostring(invoice_root), policy, payload, run + "999")


def windows_exchange(request, write, folder, approve):
    """Keep one native session open while the parent checks its preflight response."""
    if os.name != "nt":
        raise BridgeError("native sample posting requires Windows")
    folder.mkdir(exist_ok=False)
    save(folder / "preflight.request.xml", request)
    if write is not None:
        save(folder / "write.request.xml", write)
    expected = hashlib.sha256((write or "").encode()).hexdigest()
    env = os.environ.copy()
    env.update(
        KAYDBOOKS_NATIVE_DIRECTORY=str(folder),
        KAYDBOOKS_NATIVE_REQUEST_HASH=expected,
        KAYDBOOKS_NATIVE_READ_ONLY="true" if write is None else "false",
    )
    with (folder / "native.log").open("wb") as log:
        process = subprocess.Popen(
            [
                str(
                    Path(os.environ["SYSTEMROOT"])
                    / "System32/WindowsPowerShell/v1.0/powershell.exe"
                ),
                "-NoProfile",
                "-File",
                str(Path(__file__).with_name("native_invoice.ps1")),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 300
        answer = folder / "preflight.response.xml"
        while not answer.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise BridgeError(
                    "native preflight incomplete; inspect private evidence and reconcile"
                )
            time.sleep(0.1)
        response = answer.read_text(encoding="utf-8")
        try:
            allowed = approve(response)
            if write is not None:
                save(folder / ("authorize.txt" if allowed else "cancel.txt"), expected)
        except BaseException:
            if write is not None:
                save(folder / "cancel.txt", "validation stopped")
            raise
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise BridgeError("native outcome pending; never resend") from exc
        if process.returncode:
            raise BridgeError("native exchange failed; inspect private evidence and reconcile")
        receipt = folder / "add.response.xml"
        return receipt.read_text(encoding="utf-8") if receipt.exists() else None


def gate(config, actor, policy, job, now):
    config.authorize(actor, policy.id, "post-sample")
    config.authorize(actor, policy.id, "read")
    config.authorize(actor, policy.id, "validate")
    config.authorize(job["submitter"], policy.id, "submit")
    settings = policy.sample_posting
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
    if validate_source(job["source"], policy)["uncertain_fields"]:
        raise BridgeError("unresolved source fields")
    Bridge._approval(config, policy, job)
    return connector


@audited
def post(bridge, token, company, job_id, *, exchange=windows_exchange, read_exchange=None):
    config, actor, policy, store = bridge._context(token, company, "post-sample")
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            job = store.job(db, job_id)
            connector = gate(config, actor, policy, job, bridge.clock())
            if job["state"] != "queued":
                raise BridgeError(
                    "sample posting requires queued job; never retry a dispatched invoice"
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
                db.execute("SELECT COUNT(*) FROM native_invoice_attempts").fetchone()[0]
                >= policy.sample_posting["max_invoices"]
            ):
                raise BridgeError("sample dispatch quota reached")
            attempt = uuid.uuid4().hex
            run = str(int(time.time() * 1000))[-12:]
            request = add_request(policy, job["payload"], run + "998")
            context = context_hash(policy, job, connector)
            db.execute(
                "INSERT INTO native_invoice_attempts VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    attempt,
                    connector.id,
                    actor,
                    bridge.clock(),
                    request,
                    context,
                    policy.sample_posting["authorization"],
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
                "sample_dispatch_prepared",
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
                matched = check_preflight(
                    response, current_policy, current["payload"], connector, run
                )
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    job_id,
                    "sample_write_authorized" if matched is None else "sample_duplicate_found",
                    {"preflight_hash": digest(response)},
                )
            return matched is None

        folder = store.path.parent / ("native-invoice-" + attempt)
        try:
            response = exchange(preflight(policy, job["payload"], run), request, folder, approve)
            if response is not None:
                matched = validate_receipt(
                    response, policy, job["payload"], run + "998", operation="InvoiceAdd"
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
                        "native_outcome_unknown",
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
                "SELECT * FROM native_invoice_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            if (
                record is None
                or record["actor"] != actor
                or job["state"] not in ("unknown", "posted-unverified")
            ):
                raise BridgeError("owned uncertain native invoice required")
            connector = config.connectors[record["connector"]]
            if connector.company != company or not store.verify_audit(db):
                raise BridgeError("native reconciliation binding or audit invalid")
            if context_hash(policy, job, connector) != record["context_hash"]:
                raise BridgeError("native reconciliation requires original dispatch context")
        matched = None
        run = str(int(time.time() * 1000))[-12:]

        def receive(response):
            nonlocal matched
            matched = check_preflight(response, policy, job["payload"], connector, run)
            return False

        folder = store.path.parent / ("native-reconcile-" + uuid.uuid4().hex)
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
        receipt_check={"txn_id": matched["txn_id"], "payload": job["payload"]},
        **kwargs,
    )
    from .receipt_evidence import resolve

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
        )
        if proof["receipt"]["txn_id"] != matched["txn_id"]:
            raise BridgeError("native reconciliation identity changed")
        if (
            context_hash(latest_policy, current, latest.connectors[record["connector"]])
            != record["context_hash"]
        ):
            raise BridgeError("native dispatch context changed during reconciliation")
        add_response = (
            store.path.parent / ("native-invoice-" + record["attempt"]) / "add.response.xml"
        )
        dispatched = None
        if add_response.exists():
            correlation = fromstring(record["request"])[0][0].get("requestID")
            validate_receipt(
                add_response.read_text(encoding="utf-8"),
                latest_policy,
                current["payload"],
                correlation,
                operation="InvoiceAdd",
                txn_id=matched["txn_id"],
            )
            dispatched = True
        elif db.execute(
            "SELECT 1 FROM audit WHERE job_id=? AND event='sample_duplicate_found'", (job_id,)
        ).fetchone():
            dispatched = False
        proof.update(origin="native-attempt-readback", bridge_dispatched=dispatched)
        db.execute(
            "UPDATE jobs SET state='verified',txn_id=?,detail='native_invoice_verified' WHERE id=?",
            (matched["txn_id"], job_id),
        )
        store.event(db, bridge.clock(), actor, job_id, "native_invoice_verified", proof)
        return store.job(db, job_id)
