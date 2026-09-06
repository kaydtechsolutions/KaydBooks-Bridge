"""Authenticated, durable read-only SDK discovery; never accepts transaction XML."""

from __future__ import annotations

import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .config import BridgeError
from .qbwc import ACTIVE_STATES, UNCONFIRMED_IDENTITY, DurableQBWCDiscoveryService


@contextmanager
def company_lock(path):
    """OS releases the lock on process death; no stale lock deletion is necessary."""
    if path.is_symlink():
        raise BridgeError("SDK lock must not be a symlink")
    with path.open("a+b") as file:
        if os.fstat(file.fileno()).st_size == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BridgeError("direct SDK company session is busy") from exc
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def windows_exchange(request: str, destination: Path):
    """Private file IPC; credentials and company XML never appear in command arguments."""
    if os.name != "nt":
        raise BridgeError("direct SDK requires Windows")
    request_file = destination.with_suffix(".request.xml")
    if request_file.is_symlink():
        raise BridgeError("SDK request must not be a symlink")
    request_file.write_text(request, encoding="utf-8")
    env = os.environ.copy()
    env["KAYDBOOKS_SDK_REQUEST"] = str(request_file)
    env["KAYDBOOKS_SDK_RESPONSE"] = str(destination)
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(Path(__file__).with_name("direct_sdk.ps1")),
        ],
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise BridgeError(
            "SDK exchange failed; private evidence retained; explicit recovery required"
        )


def discover(
    service: DurableQBWCDiscoveryService,
    token: str,
    connector_id: str,
    password: str,
    run_id: str,
    *,
    exchange=windows_exchange,
    recover_read: bool = False,
    accounts: bool = False,
    list_id: str | None = None,
    invoice_check: dict | None = None,
    master_preview: bool = False,
) -> dict:
    """Run/resume fixed US qbXML 17 discovery under company permissions and audit.

    Unknown reads are held unless recover_read is explicit. This API cannot write.
    Real SDK results are recorded separately from QBWC callback sessions.
    """
    if not re.fullmatch(r"[1-9][0-9]{0,15}", run_id):
        raise BridgeError("SDK run id must be 1-16 decimal digits")
    config = service.config
    actor = config.authenticate(token)
    connector = config.authenticate_connector(connector_id, password)
    config.authorize(actor, connector.company, "read")
    if recover_read:
        config.authorize(actor, connector.company, "recover")
    if connector.identity_sha256 == UNCONFIRMED_IDENTITY:
        raise BridgeError("company binding is not operator-confirmed")
    store = service._stores[connector.company]
    request = service._discovery_request(run_id, "17.0")
    if type(accounts) is not bool or type(master_preview) is not bool:
        raise BridgeError("invalid lookup mode")
    from .account_lookup import append_query, validate_list_id

    validate_list_id(list_id)
    if accounts and list_id is not None:
        raise BridgeError("select preview or exact account, not both")
    lookup = accounts or list_id is not None
    operation = (
        "exact-account"
        if list_id is not None
        else ("active-account-preview" if accounts else "discovery")
    )
    if lookup:
        request = append_query(request, run_id, list_id=list_id)
    if master_preview and (lookup or invoice_check is not None):
        raise BridgeError("master preview cannot be combined with another mode")
    if master_preview:
        from .invoice_compatibility import preview_request

        request = preview_request(request, run_id)
        operation = "invoice-master-preview"
    check = None
    context_hash = None
    if invoice_check is not None:
        if lookup:
            raise BridgeError("select account lookup or invoice compatibility, not both")
        from .invoice_compatibility import append_queries, plan

        company = config.authorize(actor, connector.company, "validate")
        check = plan(company, invoice_check)
        context_hash = check["context_sha256"]
        request = append_queries(request, run_id, check)
        operation = "invoice-master-compatibility"
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            service._expire_active(db, store, time.time())
            if db.execute(
                f"SELECT 1 FROM qbwc_sessions WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)})",
                ACTIVE_STATES,
            ).fetchone():
                raise BridgeError("QBWC company session is active")
            row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (run_id,)).fetchone()
            if row is None:
                if db.execute(
                    "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
                ).fetchone():
                    raise BridgeError("resume the existing SDK discovery first")
                db.execute(
                    "INSERT INTO sdk_discovery(id,connector,actor,request,state,context_hash) VALUES(?,?,?,?,'prepared',?)",
                    (run_id, connector_id, actor, request, context_hash),
                )
                store.event(
                    db,
                    time.time(),
                    actor,
                    None,
                    "sdk_discovery_prepared",
                    {
                        "run": run_id,
                        "connector": connector_id,
                        "transport": "direct-sdk",
                        "operation": operation,
                        "context_hash": context_hash,
                    },
                )
                row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (run_id,)).fetchone()
            if (
                row["connector"] != connector_id
                or row["actor"] != actor
                or row["request"] != request
                or row["context_hash"] != context_hash
            ):
                raise BridgeError("SDK run ownership mismatch")
            if row["state"] == "blocked":
                raise BridgeError("SDK discovery is blocked")
        destination = store.path.parent / f"sdk-{run_id}.response.xml"
        if destination.is_symlink():
            raise BridgeError("SDK evidence must not be a symlink")
        response = row["response"]
        if response is None and destination.exists():
            response = destination.read_text(encoding="utf-8-sig")
        if response is None:
            if row["state"] == "dispatched" and not recover_read:
                raise BridgeError("read outcome missing; explicit read recovery required")
            with store.transaction() as db:
                db.execute("UPDATE sdk_discovery SET state='dispatched' WHERE id=?", (run_id,))
                store.event(
                    db,
                    time.time(),
                    actor,
                    None,
                    "sdk_read_dispatch",
                    {"run": run_id, "explicit_recovery": recover_read},
                )
            exchange(request, destination)
            response = destination.read_text(encoding="utf-8-sig")
        with store.transaction() as db:
            db.execute("UPDATE sdk_discovery SET response=? WHERE id=?", (response, run_id))
        try:
            discovery_response = response
            account_records = None
            master_records = None
            if lookup:
                from .account_lookup import validate_response

                discovery_response, account_records = validate_response(response, run_id, list_id)
            if master_preview:
                from .invoice_compatibility import preview_response

                discovery_response, master_records = preview_response(response, run_id)
            if check is not None:
                from .invoice_compatibility import validate_response as validate_masters_response

                discovery_response = validate_masters_response(response, run_id, check)
            identity, host = service._verify_discovery_response(
                discovery_response,
                {"correlation": run_id, "country": "US", "qbxml_version": "17.0"},
                connector,
            )
        except Exception:
            with store.transaction() as db:
                if row["state"] != "verified":
                    db.execute(
                        "UPDATE sdk_discovery SET state='blocked',error='binding or response validation failed' WHERE id=?",
                        (run_id,),
                    )
                store.event(db, time.time(), actor, None, "sdk_discovery_rejected", {"run": run_id})
            raise BridgeError("SDK binding or response validation failed") from None
        with store.transaction() as db:
            db.execute("UPDATE sdk_discovery SET state='verified' WHERE id=?", (run_id,))
            store.event(
                db,
                time.time(),
                actor,
                None,
                "sdk_discovery_verified",
                {"run": run_id, "identity_hash": identity, "transport": "direct-sdk"},
            )
        result = {
            "run": run_id,
            "state": "verified",
            "transport": "direct-sdk",
            "live_posting": False,
        }
        if lookup:
            result.update(
                accounts=account_records,
                limit=1 if list_id is not None else 20,
                complete=False,
                operation=operation,
            )
        if check is not None:
            result.update(
                operation=operation,
                scope="master-evidence-only",
                compatibility="matched",
                context_sha256=context_hash,
                service_item_count=check["item_count"],
                currency_basis="configured-single-currency"
                if check["currency_id"] is None
                else "verified-home-currency",
            )
        if master_preview:
            result.update(
                operation=operation, masters=master_records, complete=False, limit_per_entity=20
            )
        return result


def main(argv=None):
    import argparse
    import json
    import sys

    from .deployment import load_secret_file

    parser = argparse.ArgumentParser(description="Durable read-only direct SDK discovery")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--credentials", required=True, help="private secret file, never a password"
    )
    parser.add_argument("--principal", required=True)
    parser.add_argument("--connector", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--recover-read", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--accounts", action="store_true", help="preview at most 20 active accounts")
    modes.add_argument("--list-id", help="read one exact active account by ListID")
    modes.add_argument(
        "--invoice-check", type=Path, help="private invoice payload JSON for master compatibility"
    )
    modes.add_argument(
        "--master-preview", action="store_true", help="bounded private invoice master preview"
    )
    args = parser.parse_args(argv)
    try:
        load_secret_file(args.credentials)
        service = DurableQBWCDiscoveryService.from_path(args.config)
        principal = service.config.principals[args.principal]
        connector = service.config.connectors[args.connector]
        result = discover(
            service,
            os.environ.get(principal["token_env"], ""),
            args.connector,
            os.environ.get(connector.password_env, ""),
            args.run_id,
            recover_read=args.recover_read,
            accounts=args.accounts,
            master_preview=args.master_preview,
            list_id=args.list_id,
            invoice_check=json.loads(args.invoice_check.read_text(encoding="utf-8"))
            if args.invoice_check
            else None,
        )
        print(json.dumps(result))
        return 0
    except BridgeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError):
        print(json.dumps({"error": "invalid or inaccessible private input"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
