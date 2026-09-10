"""Resolve an explicit native length rejection only after an independent absence read."""

import hashlib

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .validation import canonical, digest


def resolve(bridge, token, company, store, job, attempt, run, absence_response):
    from .sample_supplier_payment_posting import context_hash

    folder = store.path.parent / ("native-supplier-payment-" + attempt["attempt"])
    names = ("closed.txt", "write-intent.txt", "write.request.xml", "add.response.xml")
    if not all((folder / name).is_file() and not (folder / name).is_symlink() for name in names):
        return None
    raw_request = attempt["request"].encode("utf-8")
    request_hash = hashlib.sha256(raw_request).hexdigest()
    if (folder / "write.request.xml").read_bytes() != raw_request or (
        folder / "write-intent.txt"
    ).read_text(encoding="utf-8") != request_hash:
        raise BridgeError("supplier payment rejection request proof differs")
    raw_failure = (folder / "add.response.xml").read_text(encoding="utf-8")
    root = fromstring(raw_failure)
    correlation = fromstring(attempt["request"])[0][0].get("requestID")
    if not (
        root.tag == "QBXML"
        and len(root) == 1
        and root[0].tag == "QBXMLMsgsRs"
        and len(root[0]) == 1
        and root[0][0].tag == "BillPaymentCheckAddRs"
        and root[0][0].get("requestID") == correlation
        and root[0][0].get("statusCode") == "3070"
        and root[0][0].get("statusSeverity") == "Error"
        and len(root[0][0]) == 0
    ):
        return None
    latest, actor, policy, _ = bridge._context(token, company, "recover")
    latest.authorize(actor, company, "read")
    latest.authorize(actor, company, "validate")
    if (
        context_hash(policy, job, latest.connectors[attempt["connector"]], recovering=True)
        != attempt["context_hash"]
    ):
        raise BridgeError("supplier payment rejection context changed")
    proof = {
        "status_code": "3070",
        "response_sha256": digest(raw_failure),
        "request_sha256": request_hash,
        "absence_run": run,
        "absence_response": absence_response,
        "absence_response_sha256": digest(absence_response),
        "attempt": attempt["attempt"],
        "retry_authorized": False,
    }
    with store.transaction() as db:
        current = store.job(db, job["id"])
        if (
            current["state"] != "unknown"
            or current["txn_id"] is not None
            or current["attempt"] != attempt["attempt"]
            or not store.verify_audit(db)
        ):
            raise BridgeError("supplier payment rejection state or audit changed")
        db.execute(
            "INSERT INTO native_supplier_payment_rejections VALUES (?,?)",
            (job["id"], canonical(proof)),
        )
        db.execute(
            "UPDATE jobs SET state='failed',detail='native_supplier_payment_rejected_3070' WHERE id=?",
            (job["id"],),
        )
        store.event(db, bridge.clock(), actor, job["id"], "native_supplier_payment_rejected", proof)
        return store.job(db, job["id"])
