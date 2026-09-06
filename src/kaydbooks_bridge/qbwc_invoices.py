"""One-update QBWC invoice master checks; no invoice posting API."""

import argparse
import json
import os
import time
from pathlib import Path

from .config import BridgeError, identifier
from .invoice_compatibility import plan, validate_response
from .validation import canonical


def current_plan(service, job, connector):
    company = service.config.authorize(job["actor"], connector.company, "validate")
    service.config.authorize(job["actor"], connector.company, "read")
    if job["connector"] != connector.id:
        raise BridgeError("invoice lookup connector mismatch")
    check = plan(company, json.loads(job["payload"]))
    if check["context_sha256"] != job["context_hash"]:
        raise BridgeError("invoice lookup policy changed; use a new job")
    return check


def invoice_job(service, token, connector_id, job_id, *, payload=None, enqueue=False):
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
            check = plan(company, payload)
            if any(
                db.execute(
                    f"SELECT 1 FROM {table} WHERE connector=? AND ticket IS NULL", (connector_id,)
                ).fetchone()
                for table in ("qbwc_account_jobs", "qbwc_invoice_jobs")
            ):
                raise BridgeError("connector already has a queued read job")
            db.execute(
                "INSERT INTO qbwc_invoice_jobs(id,actor,connector,payload,context_hash) VALUES(?,?,?,?,?)",
                (job_id, actor, connector_id, canonical(payload), check["context_sha256"]),
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
            discovery = validate_response(row["response_xml"], row["correlation"], check)
            service._verify_discovery_response(discovery, row, connector)
            result.update(
                operation="invoice-master-compatibility",
                transport="qbwc",
                compatibility="matched",
                scope="master-evidence-only",
                context_sha256=check["context_sha256"],
                service_item_count=check["item_count"],
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
        )
        print(json.dumps(result))
        return 0
    except (BridgeError, OSError, ValueError, KeyError):
        print(json.dumps({"error": "invoice check rejected; inspect private policy and evidence"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
