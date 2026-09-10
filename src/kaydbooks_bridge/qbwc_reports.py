"""Fresh, company-bound reports through the existing read-only QBWC queue."""

from typing import Literal

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .native_reports import plan as native_plan

ReportName = Literal[
    "profit-loss",
    "balance-sheet",
    "trial-balance",
    "customer-balances",
    "vendor-balances",
    "inventory-valuation",
    "inventory-stock",
    "inventory-by-site",
    "sales-customers",
    "sales-items",
    "purchases-vendors",
    "purchases-items",
    "unpaid-invoices",
    "unpaid-bills",
    "customer-statement",
    "vendor-statement",
    "general-ledger",
    "receivables-aging",
    "payables-aging",
    "job-profitability",
    "time-by-job",
    "check-detail",
    "deposit-detail",
    "journal",
]

CATEGORIES = {
    "11 Company & Financial": ["profit-loss", "balance-sheet"],
    "12 Customers & Receivables": [
        "customer-balances",
        "receivables-aging",
        "unpaid-invoices",
        "customer-statement",
    ],
    "13 Sales": ["sales-customers", "sales-items"],
    "14 Jobs, Time & Mileage": ["job-profitability", "time-by-job"],
    "15 Vendors & Payables": [
        "vendor-balances",
        "payables-aging",
        "unpaid-bills",
        "vendor-statement",
    ],
    "16 Purchases": ["purchases-vendors", "purchases-items"],
    "17 Inventory": ["inventory-valuation", "inventory-stock", "inventory-by-site"],
    "19 Banking": ["check-detail", "deposit-detail"],
    "20 Accountant & Taxes": ["trial-balance", "general-ledger", "journal"],
}


def plan(policy, specification):
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


def read(
    bridge,
    token,
    company,
    connector_id,
    request_id,
    report,
    date_to,
    date_from=None,
    basis="Accrual",
    entity_list_id=None,
    item_list_id=None,
    columns_by=None,
):
    from .qbwc import DurableQBWCDiscoveryService
    from .qbwc_invoices import invoice_job

    config, _, _, _ = bridge._context(token, company, "report")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company:
        raise BridgeError("exact assigned-company connector required")
    svc = DurableQBWCDiscoveryService.from_path(bridge.config_path)
    if svc.config.connectors.get(connector_id) != connector:
        raise BridgeError("connector configuration changed during report request")
    specification = {"report": report, "date_to": date_to, "basis": basis}
    specification.update(
        {
            k: v
            for k, v in {
                "date_from": date_from,
                "entity_list_id": entity_list_id,
                "item_list_id": item_list_id,
                "columns_by": columns_by,
            }.items()
            if v is not None
        }
    )
    result = invoice_job(
        svc,
        token,
        connector_id,
        request_id,
        enqueue=True,
        payload=specification,
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
