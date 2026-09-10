"""Fresh company-bound QuickBooks reference data through QBWC."""

from qbwc_kit._xml import fromstring

from .config import BridgeError


def read(bridge, token, company, connector_id, request_id):
    from .qbwc import DurableQBWCDiscoveryService
    from .qbwc_invoices import invoice_job

    config, _, _, _ = bridge._context(token, company, "report")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("exact assigned-company connector required")
    service = DurableQBWCDiscoveryService.from_path(bridge.config_path)
    result = invoice_job(
        service,
        token,
        connector_id,
        request_id,
        enqueue=True,
        payload={},
        operation="reference-data.read",
    )
    if "catalogs" not in result:
        result["pending"] = result["state"] in ("queued", "authenticated", "request-sent")
        if result["pending"]:
            result["next"] = "Wait for Web Connector, then repeat these exact parameters."
        else:
            result["ok"] = False
            result["error"] = "Reference-data read failed; inspect Web Connector."
    return result


def result_metadata(service, row, connector, discovery, data):
    age = float(service.clock()) - row["created_at"]
    if not 0 <= age <= 300:
        raise BridgeError("reference-data evidence is stale; use a new request_id")
    root = fromstring(discovery)
    name = root.findtext("./QBXMLMsgsRs/CompanyQueryRs/CompanyRet/CompanyName")
    if not name:
        raise BridgeError("verified company display name missing")
    return {
        "operation": "reference-data.read",
        "transport": "qbwc",
        "company": connector.company,
        "company_display_name": name,
        "company_identity_verified": True,
        "pending": False,
        "read_started_at": row["created_at"],
        "age_seconds": age,
        "source": "quickbooks-web-connector",
        **data,
    }
