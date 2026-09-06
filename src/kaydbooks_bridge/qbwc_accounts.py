"""Explicit, authenticated one-update account preview scheduling for QBWC."""

import argparse
import json
import os
import time

from .account_lookup import validate_response
from .config import BridgeError, identifier
from .deployment import load_secret_file
from .qbwc import UNCONFIRMED_IDENTITY, DurableQBWCDiscoveryService


def account_job(service, token, connector_id, job_id, *, enqueue=False):
    actor = service.config.authenticate(token)
    job_id = identifier(job_id)
    connector = service.config.connectors.get(connector_id)
    if connector is None:
        raise BridgeError("unknown connector")
    service.config.authorize(actor, connector.company, "read")
    if connector.identity_sha256 == UNCONFIRMED_IDENTITY:
        raise BridgeError("operator-confirmed company binding required")
    store = service._stores[connector.company]
    with store.transaction() as db:
        job = db.execute("SELECT * FROM qbwc_account_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            if not enqueue:
                raise BridgeError("unknown account lookup job")
            if db.execute(
                "SELECT 1 FROM qbwc_account_jobs WHERE connector=? AND ticket IS NULL",
                (connector_id,),
            ).fetchone():
                raise BridgeError("connector already has a queued account lookup")
            db.execute(
                "INSERT INTO qbwc_account_jobs VALUES(?,?,?,NULL)", (job_id, actor, connector_id)
            )
            store.event(
                db,
                time.time(),
                actor,
                None,
                "qbwc_account_lookup_queued",
                {"lookup": job_id, "connector": connector_id},
            )
            return {"job": job_id, "state": "queued", "live_posting": False}
        if job["actor"] != actor or job["connector"] != connector_id:
            raise BridgeError("account lookup ownership mismatch")
        if job["ticket"] is None:
            return {"job": job_id, "state": "queued", "live_posting": False}
        row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (job["ticket"],)).fetchone()
        if row is None:
            raise BridgeError("missing account session evidence")
        result = {"job": job_id, "state": row["state"], "live_posting": False}
        if (
            row["state"] in ("verified", "closed")
            and row["response_result"] == 100
            and not row["last_error"]
        ):
            payload, records = validate_response(row["response_xml"], row["correlation"])
            service._verify_discovery_response(payload, row, connector)
            result.update(accounts=records, limit=20, complete=False)
        store.event(db, time.time(), actor, None, "qbwc_account_lookup_read", {"lookup": job_id})
        return result


def main():
    parser = argparse.ArgumentParser(description="Queue/read one bounded QBWC account preview")
    for name in ("config", "credentials", "principal", "connector", "job"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--enqueue", action="store_true")
    args = parser.parse_args()
    try:
        load_secret_file(args.credentials)
        service = DurableQBWCDiscoveryService.from_path(args.config)
        principal = service.config.principals[args.principal]
        result = account_job(
            service,
            os.environ.get(principal["token_env"], ""),
            args.connector,
            args.job,
            enqueue=args.enqueue,
        )
        print(json.dumps(result))
    except (BridgeError, OSError, ValueError, KeyError):
        print(
            json.dumps(
                {"error": "account lookup rejected; check private configuration and job state"}
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
