"""Fresh, company-bound customer balances through the existing read-only QBWC queue."""

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .native_reports import plan as native_plan


def plan(policy, specification):
    if (
        not isinstance(specification, dict)
        or specification.get("report") != "customer-balances"
        or set(specification)
        != {
            "report",
            "date_to",
            "basis",
        }
    ):
        raise BridgeError("QBWC reports currently support unfiltered customer-balances only")
    return {**native_plan(policy, specification), "operation": "report.read"}


def result_metadata(service, row, connector, discovery, report):
    # Session start is immutable; repeated close callbacks cannot freshen old balances.
    age = float(service.clock()) - row["created_at"]
    if not 0 <= age <= 300:
        raise BridgeError("report evidence is stale; request a fresh report with a new request_id")
    root = fromstring(discovery)
    name = root.findtext("./QBXMLMsgsRs/CompanyQueryRs/CompanyRet/CompanyName")
    if not name:
        raise BridgeError("verified company display name missing")
    return {
        "operation": "report.read",
        "transport": "qbwc",
        "company": connector.company,
        "company_display_name": name,
        "company_identity_verified": True,
        "pending": False,
        "report": {
            **report,
            "read_started_at": row["created_at"],
            "age_seconds": age,
            "derived": False,
            "source": "quickbooks-web-connector",
        },
    }


def read(bridge, token, company, connector_id, request_id, report, date_to):
    from .qbwc import DurableQBWCDiscoveryService
    from .qbwc_invoices import invoice_job

    config, _, _, _ = bridge._context(token, company, "report")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("exact assigned-company connector required")
    svc = DurableQBWCDiscoveryService.from_path(bridge.config_path)
    if svc.config.connectors.get(connector_id) != connector:
        raise BridgeError("connector configuration changed during report request")
    result = invoice_job(
        svc,
        token,
        connector_id,
        request_id,
        enqueue=True,
        payload={"report": report, "date_to": date_to, "basis": "Accrual"},
        operation="report.read",
    )
    if "report" not in result:
        result["pending"] = result["state"] in ("queued", "authenticated", "request-sent")
        if result["pending"]:
            result["next"] = "Wait for Web Connector, then repeat these exact parameters."
        else:
            result["ok"] = False
            result["error"] = "Report check failed; no balances released. Inspect Web Connector."
    return result
