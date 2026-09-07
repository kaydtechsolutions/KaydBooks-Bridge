"""Search verified Bridge history; selections are never current-balance evidence."""

import re

from qbwc_kit._xml import fromstring

from .config import BridgeError, identifier
from .service import audited
from .validation import digest

TARGETS = {
    "customer-payment.create": ("invoice",),
    "supplier-payment.create": ("bill",),
    "customer-credit.create": ("invoice",),
    "supplier-credit.create": ("bill",),
    "customer-refund.create": ("customer-credit",),
    "customer-credit.apply": ("invoice", "customer-credit"),
    "supplier-credit.apply": ("bill", "supplier-credit"),
}
RECORDS = {
    "invoice": ("invoice.create", "Invoice", "CustomerRef", "subtotal"),
    "bill": ("bill.create", "Bill", "VendorRef", "total"),
    "customer-credit": ("customer-credit.create", "CreditMemo", "CustomerRef", "subtotal"),
    "supplier-credit": ("supplier-credit.create", "VendorCredit", "VendorRef", "total"),
}


def saved(db, job, connector, entity, party_field, party_id, total_field):
    proof = job.get("transaction_receipt")
    if not proof or proof.get("identity_sha256") != connector.identity_sha256:
        return None
    reference = proof.get("reference", {})
    if reference.get("connector") != connector.id:
        return None
    if reference.get("transport") == "direct-sdk":
        row = db.execute(
            "SELECT response,request FROM sdk_discovery WHERE id=? AND connector=? AND state='verified' AND error=''",
            (reference.get("id"), connector.id),
        ).fetchone()
    elif reference.get("transport") == "qbwc-posting":
        row = db.execute(
            "SELECT p.response,s.request FROM qbwc_invoice_runs r "
            "JOIN qbwc_invoice_attempts a ON a.job_id=r.job_id "
            "JOIN qbwc_invoice_steps s ON s.run_id=r.id AND s.phase='lookup' "
            "JOIN qbwc_invoice_responses p ON p.run_id=r.id AND p.phase='lookup' "
            "WHERE r.id=? AND a.connector=? AND r.job_id=? AND r.phase='done' AND p.result=100",
            (reference.get("id"), connector.id, job["id"]),
        ).fetchone()
    elif reference.get("transport") == "qbwc":
        row = db.execute(
            "SELECT s.response_xml,s.request_xml FROM qbwc_invoice_jobs j JOIN qbwc_sessions s ON s.ticket=j.ticket "
            "WHERE j.id=? AND s.connector=? AND s.state IN ('verified','closed') "
            "AND s.response_result=100 AND s.last_error=''",
            (reference.get("id"), connector.id),
        ).fetchone()
    else:
        return None
    if row is None or digest(row[0]) != proof.get("response_sha256"):
        raise BridgeError("verified transaction response is missing or changed")
    queries = [
        q
        for q in fromstring(row[1]).findall(f"QBXMLMsgsRq/{entity}QueryRq")
        if q.findtext("TxnID") == job["txn_id"]
    ]
    if len(queries) != 1:
        raise BridgeError("verified transaction selector is ambiguous")
    records = [
        r
        for response in fromstring(row[0]).findall(f"QBXMLMsgsRs/{entity}QueryRs")
        if response.get("requestID") == queries[0].get("requestID")
        for r in response.findall(entity + "Ret")
        if r.findtext("TxnID") == job["txn_id"]
    ]
    if len(records) != 1:
        raise BridgeError("verified transaction identity is ambiguous")
    record = records[0]
    if record.findtext(party_field + "/ListID") != party_id:
        return None
    receipt = proof.get("receipt", {})
    if (
        receipt.get("txn_id") != job["txn_id"]
        or record.findtext("RefNumber") != job["payload"]["ref_number"]
    ):
        raise BridgeError("verified transaction receipt differs")
    return {
        "job_id": job["id"],
        "txn_id": job["txn_id"],
        "reference": record.findtext("RefNumber"),
        "date": record.findtext("TxnDate"),
        "original_amount": receipt[total_field],
        "currency": job["payload"]["currency"],
        "verified_at": proof["observed_at"],
    }


@audited
def search(bridge, token, company, connector_id, operation, party_id, kind, search, cursor=""):
    config, actor, policy, store = bridge._context(token, company, "read")
    if not isinstance(operation, str) or kind not in TARGETS.get(operation, ()):
        raise BridgeError("transaction selection does not match operation")
    identifier(party_id)
    identifier(connector_id)
    if (
        not isinstance(search, str)
        or len(search) > 80
        or any(ord(c) < 32 for c in search)
        or not isinstance(cursor, str)
        or (cursor and (not re.fullmatch(r"[1-9][0-9]{0,18}", cursor) or int(cursor) > 2**63 - 1))
    ):
        raise BridgeError("bounded literal search and page cursor required")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company or connector.identity_sha256 == "0" * 64:
        raise BridgeError("confirmed company connector required")
    customer = operation.startswith("customer")
    group = "customers" if customer else "vendors"
    mapping = (
        policy.payment_masters
        if operation in ("customer-payment.create", "customer-refund.create")
        else policy.supplier_payment_masters
        if operation in ("supplier-payment.create", "supplier-credit.apply")
        else policy.invoice_masters
        if customer
        else policy.bill_masters
    )
    native_party = mapping.get(group, {}).get(party_id)
    if native_party is None:
        raise BridgeError("select a mapped customer or supplier")
    source_operation, entity, party_field, total_field = RECORDS[kind]
    with store.transaction() as db:
        if not store.verify_audit(db):
            raise BridgeError("transaction selection requires intact audit")
        candidates = db.execute(
            "SELECT j.rowid AS position,j.id FROM jobs j LEFT JOIN job_revisions r ON r.parent_id=j.id "
            "WHERE j.state='verified' AND j.txn_id IS NOT NULL AND j.operation=? AND r.child_id IS NULL "
            "AND j.rowid<? AND json_extract(j.payload,'$.currency')=? "
            "AND (instr(lower(json_extract(j.payload,'$.ref_number')),lower(?))>0 "
            "OR instr(lower(j.txn_id),lower(?))>0) ORDER BY j.rowid DESC LIMIT 51",
            (
                source_operation,
                int(cursor) if cursor else 2**63 - 1,
                policy.currency,
                search,
                search,
            ),
        ).fetchall()
        choices = []
        for row in candidates[:50]:
            choice = saved(
                db,
                store.job(db, row["id"]),
                connector,
                entity,
                party_field,
                native_party,
                total_field,
            )
            if choice is not None:
                choices.append(choice)
        result = {
            "company": company,
            "kind": kind,
            "choices": choices,
            "next_cursor": str(candidates[49]["position"]) if len(candidates) > 50 else None,
            "scope": "verified Bridge history; original amounts, not current balances",
            "requires_fresh_check": True,
        }
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "transaction_choices_read",
            {
                "operation": operation,
                "kind": kind,
                "party_id": party_id,
                "count": len(choices),
                "cursor": cursor,
            },
        )
    return result
