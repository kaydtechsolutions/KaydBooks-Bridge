"""Evidence inventory, not an enable switch or a claim of installed support."""

import shutil

HERMES_DOCS = "https://hermes-agent.nousresearch.com/docs/reference/tools-reference/"


def inventory() -> dict:
    surfaces = {
        "chat": "clarify / chat entry points",
        "documents": "read_file, vision_analyze; extraction quality requires testing",
        "skills_tools": "skills_list, skill_view, skill_manage; MCP extension tools",
        "scheduling": "cronjob",
        "notifications": "send_message; platform-specific credentials and recipients",
        "memory": "memory, session_search; no company authorization implied",
        "delegation": "delegate_task",
        "kanban": "conditional kanban toolset",
        "reports": "bridge-owned workflow; no verified QuickBooks report connector",
        "browser_desktop": "browser toolset, computer_use; optional dependencies",
    }
    return {
        "schema_version": 1,
        "mode": "simulation",
        "live_posting": False,
        "hermes": {
            "product_candidate": "NousResearch/hermes-agent; deployment identity unverified",
            "executable_on_path": shutil.which("hermes") is not None,
            "installed_version": None,
            "capabilities": [
                {
                    "name": name,
                    "status": "unverified",
                    "evidence_level": "public_documentation",
                    "documented_surface": surface,
                    "source": HERMES_DOCS,
                    "bridge_adapter": "planned",
                    "fallback": "company-scoped bridge CLI where implemented",
                }
                for name, surface in surfaces.items()
            ],
        },
        "quickbooks": {
            "connection": "unverified",
            "version": None,
            "country": None,
            "qbxml_versions": [],
            "company_binding": "unverified",
            "transaction_support": "unverified",
            "report_support": "unverified",
            "landed_cost": "unverified",
            "live_adapter": "disabled",
        },
        "bridge": {
            "invoice.create": "simulation_tested",
            "controlled_sample_invoice": "explicit_private_gate_required",
            "bill.create": "base_currency_expense_bill",
            "controlled_sample_bill": "explicit_private_gate_required",
            "document_intake": "implemented",
            "hermes_mcp_tools": "optional_stdio_adapter",
            "local_workflows": "implemented_no_external_deliveries",
            "receipt_register": "historical_receipts_only",
            "backup_restore": "signed_snapshot_and_isolated_drill",
            "other_transactions": "planned",
        },
    }
