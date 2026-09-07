"""Hermes entry tools use the same fixed QBWC path as the qualified browser forms."""

from .config import BridgeError, strict_keys
from .documents import confidence_schema, prepare, revise
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

CONTRACTS = {
    "prepare_upload": ({"document_id", "connector_id"}, set()),
    "check": ({"operation", "connector_id", "payload"}, set()),
    "find": ({"operation", "ref_number"}, set()),
    "prepare": (
        {"operation", "document_id", "idempotency_key", "payload", "confidence"},
        {"master_evidence"},
    ),
    "revise": (
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
    ),
    **{
        a: ({"job_id"}, set())
        for a in ("validate", "preview", "submit", "dispatch", "recover", "status")
    },
}


def parameter_schema():
    variants = []
    for action in ("prepare_upload", "check", "find", "prepare", "revise", "status"):
        required, optional = CONTRACTS[action]
        properties = {
            field: {
                "type": "object"
                if field in ("payload", "confidence", "master_evidence")
                else "string"
            }
            for field in sorted(required | optional)
        }
        if "operation" in properties:
            properties["operation"]["enum"] = sorted(OPERATIONS)
        if "connector_id" in properties:
            properties["connector_id"]["description"] = (
                "Exact connector from company_catalog_v1.connectors"
            )
        if "confidence" in properties:
            properties["confidence"].update(
                description="Flat numeric score for every payload leaf, using dot indices such as lines.0.amount. Do not include lines or other container keys. Use the exact confidence_schema returned by check; do not infer certainty from a successful master check.",
                additionalProperties={"type": "number", "minimum": 0, "maximum": 1},
                propertyNames={"pattern": r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$"},
            )
        variants.append(
            {
                "title": action
                if action != "status"
                else "validate/preview/submit/dispatch/recover/status",
                "type": "object",
                "properties": properties,
                "required": sorted(required),
                "additionalProperties": False,
            }
        )
    return {"oneOf": variants}


def call(bridge, token, company, arguments):
    strict_keys(arguments, {"action", "parameters"})
    action, params = arguments["action"], arguments["parameters"]
    if not isinstance(params, dict):
        raise BridgeError("entry parameters must be an object")
    if not isinstance(action, str) or action not in CONTRACTS:
        raise BridgeError("entry action unavailable; human approval is separate")
    required, optional = CONTRACTS[action]
    missing = required - params.keys()
    extra = params.keys() - required - optional
    if missing or extra:
        raise BridgeError(
            f"{action} parameters: required {', '.join(sorted(required))}; "
            f"optional {', '.join(sorted(optional)) or 'none'}; "
            f"missing {', '.join(sorted(missing)) or 'none'}; "
            f"unsupported {', '.join(sorted(extra)) or 'none'}"
        )
    if action == "prepare_upload":
        from .hermes_intake import prepare_upload

        return prepare_upload(bridge, token, company, **params)
    if action == "find":
        return find(bridge, token, company, **params)
    if action == "revise":
        parent = bridge.status(token, company, params["parent_id"])
        if parent["operation"] not in OPERATIONS:
            raise BridgeError("entry operation is outside the selected eight")
        return revise(bridge, token, company, **params)
    if action in ("check", "prepare"):
        if not isinstance(params["operation"], str) or params["operation"] not in OPERATIONS:
            raise BridgeError("entry operation is outside the selected eight")
        if action == "check":
            result = check_masters(bridge, token, company, **params)
            return {**result, "confidence_schema": confidence_schema(params["payload"])}
        return prepare(bridge, token, company, **params)
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


def find(bridge, token, company, operation, ref_number):
    """Read owned jobs by exact operation/reference; never resolve a conflict by writing."""
    import re

    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise BridgeError("entry operation is outside the selected eight")
    if not isinstance(ref_number, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", ref_number):
        raise BridgeError("exact transaction reference required")
    _, actor, _, store = bridge._context(token, company, "read")
    with store.transaction() as db:
        if not store.verify_audit(db):
            raise BridgeError("audit integrity failed")
        rows = db.execute(
            "SELECT id FROM jobs WHERE submitter=? AND operation=? "
            "AND json_extract(payload,'$.ref_number')=? COLLATE NOCASE ORDER BY rowid LIMIT 21",
            (actor, operation, ref_number),
        ).fetchall()
        if len(rows) > 20:
            raise BridgeError("too many matching references; inspect the company job register")
        jobs = []
        for row in rows:
            job = store.job(db, row["id"])
            jobs.append(
                {
                    k: job.get(k)
                    for k in (
                        "id",
                        "operation",
                        "state",
                        "detail",
                        "txn_id",
                        "payload",
                        "fingerprint",
                    )
                }
            )
    return {
        "company": company,
        "matches": jobs,
        "ambiguous": len(jobs) > 1,
        "posting_performed": False,
        "next": "inspect matching job status and payload; never change reference to bypass a duplicate",
    }
