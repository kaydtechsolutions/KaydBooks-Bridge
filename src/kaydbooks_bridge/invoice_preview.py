"""Deterministic review data for a validated invoice; no transport or write request."""

from decimal import Decimal

from .config import BridgeError
from .invoice_adjustments import native_lines
from .invoice_adjustments import subtotal as invoice_subtotal
from .invoice_compatibility import plan
from .validation import digest, validate_source


def build(policy, job):
    check = plan(policy, job["payload"])
    if "commercial" not in check or not job.get("master_evidence"):
        raise BridgeError("invoice preview requires linked commercial master evidence")
    source = validate_source(job["source"], policy)
    if source["uncertain_fields"]:
        raise BridgeError("invoice preview requires resolved source fields")
    invoice = check["invoice"]
    masters = policy.invoice_masters
    lines = []
    for line in invoice["lines"]:
        mapping = masters["items"][line["item_id"]]
        lines.append({**line, "master": mapping.copy()})
    subtotal = invoice_subtotal(invoice)
    tax = Decimal(invoice["tax_amount"])
    evidence = job["master_evidence"]
    review = {
        "schema": "invoice-review-v1",
        "scope": "unposted-invoice-preview",
        "company": policy.id,
        "job": job["id"],
        "fingerprint": job["fingerprint"],
        "state": job["state"],
        "live_posting": False,
        "customer": {
            "alias": invoice["customer_id"],
            "list_id": masters["customers"][invoice["customer_id"]],
        },
        "receivable_account_id": policy.account_roles["invoice_receivable"],
        "txn_date": invoice["txn_date"],
        "ref_number": invoice["ref_number"],
        "currency": invoice["currency"],
        "currency_basis": "configured-single-currency"
        if check["currency_id"] is None
        else "verified-home-currency",
        "lines": lines,
        "subtotal": format(subtotal, ".2f"),
        "tax_amount": format(tax, ".2f"),
        "total": format(subtotal + tax, ".2f"),
        "commercial_policy": check["commercial"].copy(),
        "source": {key: source[key] for key in ("namespace", "reference", "sha256")},
        "master_evidence": evidence,
        "evidence_expires_at": evidence["observed_at"] + policy.invoice_evidence_max_age_seconds,
        **(
            {
                "adjustments": invoice["adjustments"],
                "native_lines": native_lines(invoice),
                "gross_subtotal": format(sum(Decimal(line["amount"]) for line in lines), ".2f"),
                "discount_total": format(
                    sum(
                        (
                            Decimal(a["amount"])
                            for a in invoice["adjustments"]
                            if a["kind"] == "discount"
                        ),
                        Decimal(0),
                    ),
                    ".2f",
                ),
                "charge_total": format(
                    sum(
                        (
                            Decimal(a["amount"])
                            for a in invoice["adjustments"]
                            if a["kind"] == "charge"
                        ),
                        Decimal(0),
                    ),
                    ".2f",
                ),
            }
            if invoice.get("adjustments")
            else {}
        ),
    }
    return {**review, "preview_sha256": digest(review)}
