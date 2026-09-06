"""Historical register from verified receipts, not a live QuickBooks ledger report."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from .config import BridgeError, outside_repository
from .sample_posting import save
from .service import audited
from .validation import canonical, digest

INVENTORY = [
    {
        "id": "verified-invoice-register",
        "source": "historical-verified-receipts",
        "filters": ["date_from", "date_to"],
        "derived": True,
    },
    {"id": "quickbooks-financial-reports", "status": "unavailable"},
    {"id": "desktop-report-fallback", "status": "disabled"},
]


@audited
def register(bridge, token, company, date_from, date_to):
    config, actor, policy, store = bridge._context(token, company, "report")
    config.authorize(actor, company, "read")
    try:
        first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
        if first > last:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise BridgeError("valid inclusive report dates required") from exc
    with store.transaction() as db:
        if not store.verify_audit(db):
            raise BridgeError("invalid receipt report audit")
        rows, excluded = [], 0
        for row in db.execute(
            "SELECT id FROM jobs WHERE state='verified' AND operation='invoice.create' ORDER BY id"
        ):
            job = store.job(db, row["id"])
            if not first <= date.fromisoformat(job["payload"]["txn_date"]) <= last:
                continue
            proof = job.get("transaction_receipt")
            if not proof:
                excluded += 1
                continue
            connector = config.connectors.get(proof["reference"]["connector"])
            receipt = proof["receipt"]
            expected_subtotal = sum(Decimal(line["amount"]) for line in job["payload"]["lines"])
            if (
                connector is None
                or connector.company != company
                or connector.identity_sha256 != proof["identity_sha256"]
                or job["txn_id"] != receipt["txn_id"]
                or job["payload"]["currency"] != policy.currency
                or Decimal(receipt["subtotal"]) != expected_subtotal
                or Decimal(receipt["tax_amount"]) != Decimal(job["payload"].get("tax_amount", "0"))
            ):
                raise BridgeError("report receipt context or totals differ")
            rows.append(
                {
                    "job_id": job["id"],
                    "txn_id": receipt["txn_id"],
                    "ref_number": receipt["ref_number"],
                    "txn_date": job["payload"]["txn_date"],
                    "subtotal": receipt["subtotal"],
                    "tax_amount": receipt["tax_amount"],
                    "total": format(expected_subtotal + Decimal(receipt["tax_amount"]), ".2f"),
                    "observed_at": proof["observed_at"],
                    "source_response_sha256": proof["response_sha256"],
                    "current_balance_verified": False,
                }
            )
        # A transaction must never be counted twice even if provenance paths differ.
        if len({row["txn_id"] for row in rows}) != len(rows):
            raise BridgeError("duplicate transaction in receipt register")
        result = {
            "report": "verified-invoice-register",
            "company": company,
            "currency": policy.currency,
            "date_from": date_from,
            "date_to": date_to,
            "generated_at": bridge.clock(),
            "source": "historical-verified-receipts",
            "derived": True,
            "scope": "Bridge-observed invoices only; not current receivables or a complete QuickBooks ledger",
            "rows": rows,
            "total": format(sum((Decimal(row["total"]) for row in rows), Decimal(0)), ".2f"),
            "excluded_without_real_receipt": excluded,
        }
        result["report_sha256"] = digest(result)
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "receipt_register_generated",
            {"report_sha256": result["report_sha256"], "rows": len(rows)},
        )
        return result


@audited
def export(bridge, token, company, date_from, date_to, destination):
    config, actor, _, store = bridge._context(token, company, "export")
    config.authorize(actor, company, "report")
    path = outside_repository(Path(destination))
    if path.exists() or not path.parent.is_dir():
        raise BridgeError("new private report destination required")
    result = register(bridge, token, company, date_from, date_to)
    save(path, canonical(result))
    with store.transaction() as db:
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "report_exported",
            {"report_sha256": result["report_sha256"]},
        )
    return {"report_sha256": result["report_sha256"], "rows": len(result["rows"]), "exported": True}
