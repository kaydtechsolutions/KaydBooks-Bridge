import json
from pathlib import Path

from kaydbooks_bridge.config import Company, Config, Connector
from kaydbooks_bridge.deployment_walkthrough import inspect_deployment
from kaydbooks_bridge.hermes_setup import TOOLS


def write(path: Path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def deployment_request(tmp_path, monkeypatch):
    company = "company-a"
    policy = Company(
        id=company,
        simulation_identity=company,
        currency="USD",
        max_total="100.00",
        customers=("customer",),
        items=("item",),
        sources=("documents",),
        approval_required=True,
        account_roles={"invoice_receivable": "ar-id"},
        invoice_masters={"configured": True},
        bill_masters={"configured": True},
        payment_masters={"configured": True},
        inventory_transfer_masters={"configured": True},
        journal_masters={"configured": True},
        sample_posting={"connector": "connector-a"},
        sample_bill_posting={"connector": "connector-a"},
        sample_payment_posting={"connector": "connector-a"},
        sample_inventory_transfer_posting={"connector": "connector-a"},
        sample_check_posting={"connector": "connector-a"},
        sample_journal_posting={"connector": "connector-a"},
        sample_sales_receipt_posting={"connector": "connector-a"},
        sample_credit_posting={"connector": "connector-a"},
    )
    config = Config(
        tmp_path / "state",
        {company: policy},
        {
            "operator": {
                "token_env": "KAYDBOOKS_OPERATOR_SECRET",
                "companies": {company: ["read", "prepare", "validate", "submit", "post-sample"]},
            },
            "reviewer": {
                "token_env": "KAYDBOOKS_REVIEWER_SECRET",
                "companies": {company: ["approve"]},
            },
        },
        {
            "connector-a": Connector(
                "connector-a",
                company,
                "KAYDBOOKS_CONNECTOR_SECRET",
                None,
                ("CompanyName", "LegalCompanyName", "EIN"),
                "1" * 64,
            )
        },
    )
    monkeypatch.setattr("kaydbooks_bridge.deployment_walkthrough.Config.load", lambda _: config)
    monkeypatch.setattr(
        "kaydbooks_bridge.deployment_walkthrough._state_checks",
        lambda *_: {
            "state_initialized": True,
            "state_company_bound": True,
            "audit_valid": True,
            "posting_paused": True,
            "qbwc_connection_observed": True,
        },
    )
    company_file = tmp_path / "Private Company.qbw"
    company_file.write_bytes(b"private fixture")
    target = tmp_path / "target.json"
    write(
        target,
        {
            "company_id": company,
            "company_name": "Private Company Name",
            "company_file": str(company_file),
        },
    )
    credentials = tmp_path / "credentials.json"
    values = {
        "KAYDBOOKS_CONNECTOR_SECRET": "connector-" + "x" * 40,
        "KAYDBOOKS_OPERATOR_SECRET": "operator-" + "x" * 40,
        "KAYDBOOKS_REVIEWER_SECRET": "reviewer-" + "x" * 40,
    }
    write(credentials, values)
    qwc = tmp_path / "company.qwc"
    qwc.write_text(
        "<QBWCXML><AppURL>https://localhost:8443/qbwc</AppURL>"
        "<UserName>connector-a</UserName>"
        "<OwnerID>{57F3B9B0-86F1-4FCC-B1EE-566DE1813D20}</OwnerID>"
        "<FileID>{57F3B9B0-86F1-4FCC-B1EE-566DE1813D21}</FileID>"
        "<IsReadOnly>false</IsReadOnly></QBWCXML>",
        encoding="utf-8",
    )
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    for name in (
        "channel-secrets.json",
        "linux-channel.json",
        "start-tools.ps1",
        "start-channel.ps1",
        "INSTALL.txt",
    ):
        (hermes / name).write_text("{}", encoding="utf-8")
    write(
        hermes / "tools-credentials.json",
        {"KAYDBOOKS_OPERATOR_SECRET": values["KAYDBOOKS_OPERATOR_SECRET"]},
    )
    write(
        hermes / "channel-credentials.json",
        {k: values[k] for k in ("KAYDBOOKS_OPERATOR_SECRET", "KAYDBOOKS_REVIEWER_SECRET")},
    )
    write(
        hermes / "windows-channel.json",
        {
            "company": company,
            "operator_token_env": "KAYDBOOKS_OPERATOR_SECRET",
            "reviewer_token_env": "KAYDBOOKS_REVIEWER_SECRET",
        },
    )
    write(
        hermes / "hermes-mcp-fragment.json",
        {
            "mcp_servers": {
                "kaydbooks": {
                    "supports_parallel_tool_calls": False,
                    "tools": {"include": TOOLS},
                }
            }
        },
    )
    request = tmp_path / "walkthrough.json"
    write(
        request,
        {
            "config": str(tmp_path / "config.json"),
            "target": str(target),
            "credentials": str(credentials),
            "qwc": str(qwc),
            "hermes_bundle": str(hermes),
            "company": company,
            "connector": "connector-a",
            "operator": "operator",
            "reviewer": "reviewer",
        },
    )
    return request, values, qwc


def test_complete_deployment_walkthrough_is_secret_safe(tmp_path, monkeypatch):
    request, secrets, _ = deployment_request(tmp_path, monkeypatch)
    result = inspect_deployment(request)
    assert result["deployment_complete"] and not result["pending"]
    assert result["accounting_writes"] == 0 and not result["posting_changed"]
    rendered = json.dumps(result)
    assert "Private Company" not in rendered and str(tmp_path) not in rendered
    assert all(value not in rendered for value in secrets.values())


def test_wrong_qwc_and_reused_roles_do_not_qualify(tmp_path, monkeypatch):
    request, secrets, qwc = deployment_request(tmp_path, monkeypatch)
    qwc.write_text(qwc.read_text().replace("connector-a", "connector-b"), encoding="utf-8")
    credentials = Path(json.loads(request.read_text())["credentials"])
    values = json.loads(credentials.read_text())
    values["KAYDBOOKS_REVIEWER_SECRET"] = secrets["KAYDBOOKS_OPERATOR_SECRET"]
    write(credentials, values)
    result = inspect_deployment(request)
    assert not result["deployment_complete"]
    assert {"credentials_distinct", "qwc_company_connector"} <= set(result["pending"])
