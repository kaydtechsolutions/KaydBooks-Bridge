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
    "check": ({"operation", "connector_id", "payload"}, set()),
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
    for action in ("check", "prepare", "revise", "status"):
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
