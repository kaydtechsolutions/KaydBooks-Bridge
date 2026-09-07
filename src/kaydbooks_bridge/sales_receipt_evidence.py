"""Fresh, owned QBWC sales-receipt master evidence."""

from .config import BridgeError, strict_keys
from .payment_evidence import resolve_qbwc


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    if reference["transport"] != "qbwc":
        raise BridgeError("sales receipts require Web Connector evidence")
    return resolve_qbwc(
        config, policy, store, db, actor, payload, reference, now, operation="sales-receipt.create"
    )


def require(config, policy, store, db, job, now):
    saved = job.get("master_evidence")
    if (
        saved is None
        or resolve(
            config, policy, store, db, job["submitter"], job["payload"], saved["reference"], now
        )
        != saved
    ):
        raise BridgeError("fresh owned sales-receipt evidence required")
