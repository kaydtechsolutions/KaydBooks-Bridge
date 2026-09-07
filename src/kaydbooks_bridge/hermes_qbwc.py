"""Hermes entry tools use the same fixed QBWC path as the qualified browser forms."""

from .config import BridgeError, strict_keys
from .documents import prepare, revise
from .qbwc_posting import enqueue, recover
from .web_ui import check_masters

OPERATIONS = frozenset(
    {
        "invoice.create",
        "sales-receipt.create",
        "bill.create",
        "customer-payment.create",
        "customer-credit.create",
        "journal.create",
        "check.create",
        "inventory-transfer.create",
    }
)


def call(bridge, token, company, arguments):
    strict_keys(arguments, {"action", "parameters"})
    action, params = arguments["action"], arguments["parameters"]
    if not isinstance(params, dict):
        raise BridgeError("entry parameters must be an object")
    if action == "revise":
        strict_keys(
            params,
            {
                "parent_id",
                "parent_fingerprint",
                "reason",
                "document_id",
                "idempotency_key",
                "payload",
                "confidence",
            },
            {"master_evidence"},
        )
        parent = bridge.status(token, company, params["parent_id"])
        if parent["operation"] not in OPERATIONS:
            raise BridgeError("entry operation is outside the selected eight")
        return revise(bridge, token, company, **params)
    if action in ("check", "prepare"):
        required = (
            {"operation", "connector_id", "payload"}
            if action == "check"
            else {"operation", "document_id", "idempotency_key", "payload", "confidence"}
        )
        strict_keys(params, required, {"master_evidence"} if action == "prepare" else set())
        if not isinstance(params["operation"], str) or params["operation"] not in OPERATIONS:
            raise BridgeError("entry operation is outside the selected eight")
        if action == "check":
            return check_masters(bridge, token, company, **params)
        return prepare(bridge, token, company, **params)
    if action not in ("validate", "preview", "submit", "dispatch", "recover", "status"):
        raise BridgeError("entry action unavailable; human approval is separate")
    strict_keys(params, {"job_id"})
    job = bridge.status(token, company, params["job_id"])
    if job["operation"] not in OPERATIONS:
        raise BridgeError("entry operation is outside the selected eight")
    if action == "status":
        return job
    if action == "preview":
        return bridge.preview(token, company, job["id"])
    if action in ("validate", "submit"):
        return bridge.action(token, company, job["id"], action)
    return (enqueue if action == "dispatch" else recover)(bridge, token, company, job["id"])
