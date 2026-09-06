"""One-update QBWC invoice master checks; no invoice posting API."""

import argparse
import json
import os
import time
from pathlib import Path

from .config import BridgeError, identifier
from .invoice_compatibility import plan, validate_response
from .validation import canonical


def make_plan(company, payload, txn_id=None):
    if txn_id is None:
        return plan(company, payload)
    from .invoice_receipt import lookup_context

    return {
        "receipt_policy": company,
        "payload": payload,
        "txn_id": txn_id,
        "context_sha256": lookup_context(company, payload, txn_id),
    }


def append_request(request, correlation, check):
    if "txn_id" in check:
        from .invoice_receipt import append_lookup

        return append_lookup(request, correlation, check["txn_id"])
    from .invoice_compatibility import append_queries

    return append_queries(request, correlation, check)


def check_response(response, correlation, check):
    if "txn_id" in check:
        from .invoice_receipt import validate_lookup

        return validate_lookup(
            response, correlation, check["receipt_policy"], check["payload"], check["txn_id"]
        )
    return validate_response(response, correlation, check), None


def current_plan(service, job, connector):
    company = service.config.authorize(job["actor"], connector.company, "validate")
    service.config.authorize(job["actor"], connector.company, "read")
    if job["connector"] != connector.id:
        raise BridgeError("invoice lookup connector mismatch")
    check = make_plan(company, json.loads(job["payload"]), job["txn_id"])
    if check["context_sha256"] != job["context_hash"]:
        raise BridgeError("invoice lookup policy changed; use a new job")
    return check


def invoice_job(service, token, connector_id, job_id, *, payload=None, enqueue=False, txn_id=None):
    from .qbwc import UNCONFIRMED_IDENTITY

    actor = service.config.authenticate(token)
    identifier(job_id)
    connector = service.config.connectors.get(connector_id)
    if connector is None:
        raise BridgeError("unknown connector")
    company = service.config.authorize(actor, connector.company, "validate")
    service.config.authorize(actor, connector.company, "read")
    if connector.identity_sha256 == UNCONFIRMED_IDENTITY:
        raise BridgeError("operator-confirmed company binding required")
    store = service._stores[connector.company]
    with store.transaction() as db:
        job = db.execute("SELECT * FROM qbwc_invoice_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            if not enqueue or payload is None:
                raise BridgeError("new invoice check requires payload and enqueue")
            check = make_plan(company, payload, txn_id)
            if any(
                db.execute(
                    f"SELECT 1 FROM {table} WHERE connector=? AND ticket IS NULL", (connector_id,)
                ).fetchone()
                for table in ("qbwc_account_jobs", "qbwc_invoice_jobs")
            ):
                raise BridgeError("connector already has a queued read job")
            db.execute(
                "INSERT INTO qbwc_invoice_jobs(id,actor,connector,payload,context_hash,txn_id) VALUES(?,?,?,?,?,?)",
                (job_id, actor, connector_id, canonical(payload), check["context_sha256"], txn_id),
            )
            store.event(
                db,
                time.time(),
                actor,
                None,
                "qbwc_invoice_check_queued",
                {"job": job_id, "context_hash": check["context_sha256"]},
            )
            return {"job": job_id, "state": "queued", "live_posting": False}
        if job["actor"] != actor or job["connector"] != connector_id:
            raise BridgeError("invoice check ownership mismatch")
        if payload is not None and canonical(payload) != job["payload"]:
            raise BridgeError("invoice check payload is immutable")
        if (txn_id is not None or enqueue) and txn_id != job["txn_id"]:
            raise BridgeError("invoice check transaction selector is immutable")
        check = current_plan(service, job, connector)
        if job["ticket"] is None:
            return {"job": job_id, "state": "queued", "live_posting": False}
        row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (job["ticket"],)).fetchone()
        if row is None:
            raise BridgeError("invoice session evidence missing")
        result = {"job": job_id, "state": row["state"], "live_posting": False}
        if (
            row["state"] in ("verified", "closed")
            and row["response_result"] == 100
            and not row["last_error"]
        ):
            discovery, receipt = check_response(row["response_xml"], row["correlation"], check)
            service._verify_discovery_response(discovery, row, connector)
            if receipt is not None:
                result.update(
                    operation="invoice-receipt-check",
                    transport="qbwc",
                    receipt=receipt,
                    context_sha256=check["context_sha256"],
                )
                store.event(
                    db, time.time(), actor, None, "qbwc_invoice_receipt_read", {"job": job_id}
                )
                return result
            result.update(
                operation="invoice-master-compatibility",
                transport="qbwc",
                compatibility="matched",
                scope="master-evidence-only",
                context_sha256=check["context_sha256"],
                service_item_count=sum(
                    s.get("kind", "Service") == "Service" for s in check["item_specs"]
                ),
                inventory_item_count=sum(s.get("kind") == "Inventory" for s in check["item_specs"]),
                commercial_checks="matched" if "commercial" in check else "not-requested",
                currency_basis="configured-single-currency"
                if check["currency_id"] is None
                else "verified-home-currency",
            )
        store.event(
            db,
            time.time(),
            actor,
            None,
            "qbwc_invoice_check_read",
            {"job": job_id, "state": row["state"]},
        )
        return result


def main(argv=None):
    from .deployment import load_secret_file
    from .qbwc import DurableQBWCDiscoveryService

    parser = argparse.ArgumentParser(description="Queue/read one QBWC invoice master check")
    for name in ("config", "credentials", "principal", "connector", "job"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument(
        "--txn-id", help="exact saved invoice receipt lookup instead of master checks"
    )
    args = parser.parse_args(argv)
    try:
        load_secret_file(args.credentials)
        service = DurableQBWCDiscoveryService.from_path(args.config)
        principal = service.config.principals[args.principal]
        result = invoice_job(
            service,
            os.environ.get(principal["token_env"], ""),
            args.connector,
            args.job,
            payload=json.loads(args.payload.read_text(encoding="utf-8")) if args.payload else None,
            enqueue=args.enqueue,
            txn_id=args.txn_id,
        )
        print(json.dumps(result))
        return 0
    except (BridgeError, OSError, ValueError, KeyError):
        print(json.dumps({"error": "invoice check rejected; inspect private policy and evidence"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
