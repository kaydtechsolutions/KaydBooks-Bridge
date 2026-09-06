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
    commercial_preview: bool = False,
    receipt_check: dict | None = None,
    bill_preview: bool = False,
    bill_check: dict | None = None,
    expense_accounts: bool = False,
    bill_receipt_check: dict | None = None,
    bill_terms: bool = False,
    bill_services: bool = False,
    payment_check: dict | None = None,
    payment_methods: bool = False,
    payment_receipt_check: dict | None = None,
    supplier_payment_check: dict | None = None,
    supplier_payment_receipt_check: dict | None = None,
) -> dict:
    """Run/resume fixed US qbXML 17 discovery under company permissions and audit.

    Unknown reads are held unless recover_read is explicit. This API cannot write.
    Real SDK results are recorded separately from QBWC callback sessions.
    """
    supplier_payment = (
        supplier_payment_check is not None or supplier_payment_receipt_check is not None
    )
    if supplier_payment:
        if (
            payment_check is not None
            or payment_receipt_check is not None
            or payment_methods
            or (supplier_payment_check is not None and supplier_payment_receipt_check is not None)
        ):
            raise BridgeError("supplier payment check cannot be combined with another lookup")
        payment_check = supplier_payment_check
        payment_receipt_check = supplier_payment_receipt_check
    if type(payment_methods) is not bool:
        raise BridgeError("invalid payment method preview mode")
    if (payment_check is not None or payment_methods or payment_receipt_check is not None) and (
        accounts
        or list_id is not None
        or invoice_check is not None
        or master_preview
        or commercial_preview
        or receipt_check is not None
        or bill_preview
        or bill_check is not None
        or expense_accounts
        or bill_receipt_check is not None
        or bill_terms
        or bill_services
        or (payment_check is not None and payment_methods)
        or (payment_receipt_check is not None and (payment_check is not None or payment_methods))
    ):
        raise BridgeError("payment check cannot be combined with another lookup")
    if type(bill_preview) is not bool:
        raise BridgeError("invalid bill preview mode")
    if type(bill_terms) is not bool or type(bill_services) is not bool:
        raise BridgeError("invalid bill terms preview mode")
    if (bill_terms or bill_services) and (
        (bill_terms and bill_services)
        or accounts
        or expense_accounts
        or list_id is not None
        or invoice_check is not None
        or master_preview
        or commercial_preview
        or receipt_check is not None
        or bill_receipt_check is not None
        or bill_preview
        or bill_check is not None
    ):
        raise BridgeError("bill terms preview cannot be combined with another lookup")
    if bill_receipt_check is not None:
        if receipt_check is not None:
            raise BridgeError("select one receipt operation")
        receipt_check = bill_receipt_check
    if type(expense_accounts) is not bool:
        raise BridgeError("invalid expense preview mode")
    if expense_accounts:
        if accounts or list_id is not None:
            raise BridgeError("expense preview cannot be combined with another lookup")
        accounts = True
    if (bill_preview or bill_check is not None) and (
        accounts
        or list_id is not None
        or invoice_check is not None
        or master_preview
        or commercial_preview
        or receipt_check is not None
        or (bill_preview and bill_check is not None)
    ):
        raise BridgeError("bill preview cannot be combined with another lookup")
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
    if any(type(flag) is not bool for flag in (accounts, master_preview, commercial_preview)):
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
        request = append_query(request, run_id, list_id=list_id, expense_only=expense_accounts)
        if expense_accounts:
            operation = "active-expense-account-preview"
    if commercial_preview and master_preview:
        raise BridgeError("select one preview mode")
    if commercial_preview:
        master_preview = True
    if master_preview and (lookup or invoice_check is not None):
        raise BridgeError("master preview cannot be combined with another mode")
    if master_preview:
        from .invoice_compatibility import preview_request

        request = preview_request(request, run_id, commercial=commercial_preview)
        operation = "invoice-master-preview"
    check = None
    context_hash = None
    receipt_policy = None
    bill_plan = None
    payment_plan = None
    payment_policy = None
    if payment_receipt_check is not None:
        from .config import strict_keys
        from .payment_receipt import append_lookup, lookup_context

        if supplier_payment:
            from .supplier_payment_receipt import append_lookup, lookup_context

        strict_keys(payment_receipt_check, {"payload", "txn_id"})
        payment_policy = config.authorize(actor, connector.company, "validate")
        context_hash = lookup_context(
            payment_policy, payment_receipt_check["payload"], payment_receipt_check["txn_id"]
        )
        request = append_lookup(
            request,
            run_id,
            payment_policy,
            payment_receipt_check["payload"],
            payment_receipt_check["txn_id"],
        )
        operation = (
            "supplier-payment-receipt-check"
            if supplier_payment
            else "customer-payment-receipt-check"
        )
    if payment_methods:
        from .customer_payments import append_methods

        request = append_methods(request, run_id)
        operation = "payment-method-preview"
    if payment_check is not None:
        from .customer_payments import append_check
        from .customer_payments import plan as payment_master_plan

        if supplier_payment:
            from .supplier_payments import append_check
            from .supplier_payments import plan as payment_master_plan

        company = config.authorize(actor, connector.company, "validate")
        payment_plan = payment_master_plan(company, payment_check)
        context_hash = payment_plan["context_sha256"]
        request = append_check(request, run_id, payment_plan)
        operation = "supplier-payment-check" if supplier_payment else "customer-payment-check"
    if bill_terms or bill_services:
        from .bill_lookup import append_terms_preview

        request = append_terms_preview(request, run_id, services=bill_services)
        operation = "bill-services-preview" if bill_services else "bill-terms-preview"
    if bill_check is not None:
        from .bill_lookup import append_check
        from .bill_lookup import plan as bill_master_plan

        company = config.authorize(actor, connector.company, "validate")
        bill_plan = bill_master_plan(company, bill_check)
        context_hash = bill_plan["context_sha256"]
        request = append_check(request, run_id, bill_plan)
        operation = "bill-master-check"
    if bill_preview:
        from .bill_lookup import append_preview

        request = append_preview(request, run_id)
        operation = "bill-master-preview"
    if receipt_check is not None:
        from .config import strict_keys
        from .invoice_receipt import append_lookup, lookup_context

        if bill_receipt_check is not None:
            from .bill_receipt import append_lookup, lookup_context

        if lookup or master_preview or invoice_check is not None:
            raise BridgeError("receipt lookup cannot be combined with another mode")
        strict_keys(receipt_check, {"txn_id", "payload"})
        receipt_policy = config.authorize(actor, connector.company, "validate")
        context_hash = lookup_context(
            receipt_policy, receipt_check["payload"], receipt_check["txn_id"]
        )
        args = (receipt_policy, receipt_check["payload"])
        request = append_lookup(request, run_id, receipt_check["txn_id"], *args)
        operation = "invoice-receipt-check"
        if bill_receipt_check is not None:
            operation = "bill-receipt-check"
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
            receipt = None
            payment_balances = None
            if payment_policy is not None:
                from .payment_receipt import validate_lookup

                if supplier_payment:
                    from .supplier_payment_receipt import validate_lookup

                discovery_response, receipt = validate_lookup(
                    response,
                    run_id,
                    payment_policy,
                    payment_receipt_check["payload"],
                    payment_receipt_check["txn_id"],
                )
            if payment_methods:
                from .customer_payments import validate_methods

                discovery_response, master_records = validate_methods(response, run_id)
            if payment_plan is not None:
                from .customer_payments import validate_check

                if supplier_payment:
                    from .supplier_payments import validate_check

                discovery_response, payment_balances = validate_check(
                    response, run_id, payment_plan
                )
            if bill_terms or bill_services:
                from .bill_lookup import validate_terms_preview

                discovery_response, master_records = validate_terms_preview(
                    response, run_id, services=bill_services
                )
            if bill_plan is not None:
                from .bill_lookup import validate_check

                discovery_response = validate_check(response, run_id, bill_plan)
            if bill_preview:
                from .bill_lookup import validate_preview

                discovery_response, master_records = validate_preview(response, run_id)
            if receipt_policy is not None:
                from .invoice_receipt import validate_lookup

                if bill_receipt_check is not None:
                    from .bill_receipt import validate_lookup

                discovery_response, receipt = validate_lookup(
                    response,
                    run_id,
                    receipt_policy,
                    receipt_check["payload"],
                    receipt_check["txn_id"],
                )
            if lookup:
                from .account_lookup import validate_response

                discovery_response, account_records = validate_response(
                    response, run_id, list_id, expense_only=expense_accounts
                )
            if master_preview:
                from .invoice_compatibility import preview_response

                discovery_response, master_records = preview_response(
                    response, run_id, commercial=commercial_preview
                )
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
        if receipt is not None:
            result.update(operation=operation, receipt=receipt, context_sha256=context_hash)
        if bill_plan is not None:
            result.update(
                operation=operation,
                scope="master-evidence-only",
                compatibility="matched",
                context_sha256=context_hash,
            )
        if payment_plan is not None:
            result.update(
                operation=operation,
                scope="payment-allocation-review-only",
                balances=payment_balances,
                context_sha256=context_hash,
            )
        if check is not None:
            result.update(
                operation=operation,
                scope="master-evidence-only",
                compatibility="matched",
                context_sha256=context_hash,
                service_item_count=sum(
                    s.get("kind", "Service") == "Service" for s in check["item_specs"]
                ),
                inventory_item_count=sum(s.get("kind") == "Inventory" for s in check["item_specs"]),
                commercial_checks="matched" if "commercial" in check else "not-requested",
                currency_basis="configured-single-currency"
                if check["currency_id"] is None
                else "verified-home-currency",
            )
        if master_preview or bill_preview or bill_terms or bill_services or payment_methods:
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
    modes.add_argument(
        "--supplier-payment-check", type=Path, help="check exact vendor/bill allocation; no posting"
    )
    modes.add_argument(
        "--payment-methods", action="store_true", help="preview at most 20 active payment methods"
    )
    modes.add_argument(
        "--payment-check", type=Path, help="check exact customer payment allocations; no posting"
    )
    modes.add_argument("--accounts", action="store_true", help="preview at most 20 active accounts")
    modes.add_argument(
        "--bill-terms", action="store_true", help="preview at most 20 active standard terms"
    )
    modes.add_argument(
        "--bill-services",
        action="store_true",
        help="preview at most 20 active purchase service items",
    )
    modes.add_argument(
        "--expense-accounts", action="store_true", help="preview at most 20 active expense accounts"
    )
    modes.add_argument(
        "--bill-preview", action="store_true", help="bounded vendor/account preview for bill setup"
    )
    modes.add_argument("--list-id", help="read one exact active account by ListID")
    modes.add_argument(
        "--bill-check", type=Path, help="private expense bill payload JSON for master compatibility"
    )
    modes.add_argument(
        "--receipt-check",
        type=Path,
        help="private {txn_id,payload} JSON for saved invoice verification",
    )
    modes.add_argument(
        "--bill-receipt-check",
        type=Path,
        help="private {txn_id,payload} JSON for saved bill verification",
    )
    modes.add_argument(
        "--invoice-check", type=Path, help="private invoice payload JSON for master compatibility"
    )
    modes.add_argument(
        "--master-preview", action="store_true", help="bounded private invoice master preview"
    )
    modes.add_argument(
        "--commercial-preview",
        action="store_true",
        help="bounded private inventory, tax and pricing master preview",
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
            bill_terms=args.bill_terms,
            bill_services=args.bill_services,
            expense_accounts=args.expense_accounts,
            bill_preview=args.bill_preview,
            bill_check=json.loads(args.bill_check.read_text(encoding="utf-8"))
            if args.bill_check
            else None,
            master_preview=args.master_preview,
            commercial_preview=args.commercial_preview,
            list_id=args.list_id,
            receipt_check=json.loads(args.receipt_check.read_text(encoding="utf-8"))
            if args.receipt_check
            else None,
            bill_receipt_check=json.loads(args.bill_receipt_check.read_text(encoding="utf-8"))
            if args.bill_receipt_check
            else None,
            invoice_check=json.loads(args.invoice_check.read_text(encoding="utf-8"))
            if args.invoice_check
            else None,
            payment_check=json.loads(args.payment_check.read_text(encoding="utf-8"))
            if args.payment_check
            else None,
            payment_methods=args.payment_methods,
            supplier_payment_check=json.loads(
                args.supplier_payment_check.read_text(encoding="utf-8")
            )
            if args.supplier_payment_check
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
