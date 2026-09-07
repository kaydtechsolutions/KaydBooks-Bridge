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
            "selected_transactions": {
                operation: "controlled_sample_qualified"
                for operation in (
                    "sales-receipt.create",
                    "invoice.create",
                    "customer-credit.create",
                    "customer-payment.create",
                    "bill.create",
                    "journal.create",
                    "inventory-transfer.create",
                    "check.create",
                )
            },
            "selected_transaction_gate": "explicit_private_gate_required",
            "tax": "excluded_from_v0.1.0",
            "document_intake": "implemented",
            "hermes_mcp_tools": "implemented_optional_stdio_adapter",
            "hermes_confirmation_channel": "implemented_private_configuration_required",
            "local_workflows": "implemented_no_external_deliveries",
            "receipt_register": "historical_receipts_only",
            "backup_restore": "signed_snapshot_and_isolated_drill",
            "production_posting": "disabled",
            "other_transactions": "outside_v0.1.0_scope",
        },
    }
