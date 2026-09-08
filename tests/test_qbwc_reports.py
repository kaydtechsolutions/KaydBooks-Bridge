"""Real QBWC callback lifecycle with synthetic native report responses."""
# ruff: noqa: F401, F811

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.hermes_tools import server
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.qbwc_reports import read
from kaydbooks_bridge.service import Bridge
from test_direct_sdk import direct
from test_native_reports import case, response
from test_qbwc_discovery import (
    COMPANY_B,
    authenticate,
    call,
    discovery_setup,
    hcp_for,
    receive,
)


def request(path, token, **overrides):
    args = dict(
        connector_id="connector-company-a",
        request_id="balance-001",
        report="customer-balances",
        date_to="2026-09-07",
    )
    args.update(overrides)
    return read(Bridge(path), token, "company-a", **args)


def exchange(path, *, company=None, alter=None, native_type="CustomerBalanceSummary"):
    svc = S.from_path(path)
    ticket, _ = authenticate(svc)
    xml = call(
        svc,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse=hcp_for(),
        strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
        qbXMLCountry="US",
        qbXMLMajorVers="17",
        qbXMLMinorVers="0",
    )
    assert native_type in xml
    assert not any(op in xml for op in ("AddRq", "ModRq", "DelRq"))
    result = response(xml, **({"company": company} if company else {}), alter=alter)
    code = receive(svc, ticket, result)
    assert receive(svc, ticket, result) == code
    call(svc, "closeConnection", ticket=ticket)
    return svc, ticket, code


def test_report_pending_restart_readback_native_total_and_company(case):
    path, token = case
    assert request(path, token)["pending"] is True
    assert request(path, token)["job"] == "balance-001"
    svc, ticket, code = exchange(path)
    assert code == 100
    result = request(path, token)
    assert result["pending"] is False
    assert result["company_display_name"] == "Synthetic Company A"
    assert result["company_identity_verified"] is True
    assert result["report"]["complete"] is True
    assert result["report"]["native_totals"][0]["cells"]["2"]["decimal"] == "25.00"
    assert result["report"]["derived"] is False
    assert result["live_posting"] is False
    with svc._stores["company-a"].transaction() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM qbwc_invoice_jobs").fetchone()[0] == 1
        assert svc._stores["company-a"].verify_audit(db)


@pytest.mark.parametrize("change", ["company", "partial", "date"])
def test_wrong_company_or_incomplete_or_wrong_date_never_releases_balances(case, change):
    path, token = case
    request(path, token)

    def alter(root):
        if change == "partial":
            root.find(".//NumRows").text = "999"
        if change == "date":
            root.find(".//ReportSubtitle").text = "As of September 6, 2026"

    _, _, code = exchange(path, company=COMPANY_B if change == "company" else None, alter=alter)
    assert code == -1
    result = request(path, token)
    assert "report" not in result and result["ok"] is False


def test_report_needs_report_read_permissions_not_validate(case):
    path, token = case
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = ["read", "report"]
    path.write_text(json.dumps(raw))
    assert request(path, token)["pending"]
    raw["principals"][actor]["companies"]["company-a"] = ["read"]
    path.write_text(json.dumps(raw))
    svc = S.from_path(path)
    ticket, _ = authenticate(svc)
    assert (
        call(
            svc,
            "sendRequestXML",
            ticket=ticket,
            strHCPResponse=hcp_for(),
            strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
            qbXMLCountry="US",
            qbXMLMajorVers="17",
            qbXMLMinorVers="0",
        )
        == ""
    )
    with pytest.raises(BridgeError):
        request(path, token)


def test_report_rejects_changed_request_and_wrong_connector(case):
    path, token = case
    request(path, token)
    with pytest.raises(BridgeError, match="immutable"):
        request(path, token, date_to="2026-09-08")
    with pytest.raises(BridgeError, match="assigned-company"):
        request(path, token, connector_id="connector-company-b")


def test_report_stale_evidence_not_freshened_by_close(case, monkeypatch):
    from kaydbooks_bridge.qbwc_invoices import invoice_job

    path, token = case
    request(path, token)
    svc, ticket, _ = exchange(path)
    started = svc.inspect_session(ticket)["created_at"]
    svc.clock = lambda: started + 301
    with pytest.raises(BridgeError, match="stale"):
        invoice_job(svc, token, "connector-company-a", "balance-001", operation="report.read")


@pytest.mark.parametrize("overrides", [{"report": "general-ledger"}, {"date_to": "today"}])
def test_explicit_bounded_report_contract(case, overrides):
    with pytest.raises(BridgeError):
        request(*case, **overrides)


def test_mcp_report_schema_is_explicit(case):
    import asyncio

    app = server(*case)
    tools = asyncio.run(app.list_tools())
    schema = next(t.inputSchema for t in tools if t.name == "qbwc_report_v1")
    assert set(schema["required"]) == {"company", "connector_id", "request_id", "report", "date_to"}
    from kaydbooks_bridge.native_reports import REPORTS

    assert set(schema["properties"]["report"]["enum"]) == set(REPORTS)
    assert schema["properties"]["date_to"]["pattern"]


def test_all_main_reports_cross_qbwc_queue_and_readback(case):
    from kaydbooks_bridge.native_reports import REPORTS
    from test_native_reports import specification

    path, token = case
    for index, (name, (_, native, _)) in enumerate(REPORTS.items()):
        args = {**specification(name), "request_id": f"report-{index}"}
        assert request(path, token, **args)["pending"]
        _, _, code = exchange(path, native_type=native)
        assert code == 100
        result = request(path, token, **args)
        assert result["report"]["report"] == name
        assert result["report"]["complete"] is True
        assert result["report"]["native_totals"][0]["cells"]["2"]["decimal"] == "25.00"


@pytest.mark.parametrize(
    "overrides",
    [
        {"report": "profit-loss"},
        {"report": "customer-balances", "date_from": "2026-01-01"},
        {"report": "profit-loss", "date_from": "2026-10-01"},
        {"report": "customer-balances", "basis": "Cash"},
        {"report": "customer-statement", "date_from": "2026-01-01"},
        {"report": "inventory-stock", "columns_by": "Month"},
        {"report": "time-by-job", "date_from": "2026-01-01", "basis": "Cash"},
        {"report": "job-profitability", "date_from": "2026-01-01", "columns_by": "Month"},
        {"report": "sales-tax-liability"},
    ],
)
def test_report_specific_required_fields_and_unsupported_options(case, overrides):
    with pytest.raises(BridgeError):
        request(*case, **overrides)


def test_period_filters_and_cash_basis_are_immutable_and_echoed(case):
    args = {
        "report": "sales-customers",
        "date_from": "2026-01-01",
        "basis": "Cash",
        "entity_list_id": "customer-1",
        "columns_by": "Month",
    }
    request(*case, **args)
    with pytest.raises(BridgeError, match="immutable"):
        request(*case, **{**args, "basis": "Accrual"})

    def alter(root):
        root.find(".//ReportBasis").text = "Cash"
        root.find(".//ReportSubtitle").text = "January 1, 2026 - September 7, 2026"

    _, _, code = exchange(case[0], native_type="SalesByCustomerSummary", alter=alter)
    assert code == 100
    result = request(*case, **args)
    assert result["report"]["basis"] == "Cash"
    assert result["report"]["date_evidence"]["native_start_date"] == "2026-01-01"
    assert result["report"]["filters"]["entity_list_id"] == "customer-1"
