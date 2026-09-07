"""Real headless browser qualification of forms against a synthetic HTTP service.

Run explicitly with KAYDBOOKS_BROWSER_TESTS=1 after playwright install chromium.
Native reads are substituted here; these tests do not claim QuickBooks qualification.
"""
# ruff: noqa: F811

import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from kaydbooks_bridge import web_ui
from kaydbooks_bridge.config import PERMISSIONS
from test_bridge import TOKENS, setup  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("KAYDBOOKS_BROWSER_TESTS") != "1", reason="explicit installed-browser qualification"
)


@pytest.fixture
def page(setup, monkeypatch):
    from playwright.sync_api import sync_playwright

    raw = setup[2]
    raw["principals"]["operator-a"]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        account_roles={"customer_discount": "DISC-INCOME", "supplier_discount": "DISC-EXPENSE"},
        bill_masters={
            "vendors": {"vendor-a": "V-A"},
            "payable": "AP-A",
            "expenses": {"office": "E-A"},
        },
        payment_masters={
            "customers": {"customer-a": "C-A"},
            "receivable": "AR-A",
            "deposits": {"cash": "B-A"},
            "methods": {"cash": "M-A"},
        },
        supplier_payment_masters={
            "vendors": {"vendor-a": "V-A"},
            "payable": "AP-A",
            "banks": {"cash": "B-A"},
        },
    )
    setup[1].write_text(json.dumps(raw))
    monkeypatch.setattr(web_ui, "check_masters", lambda *a, **kw: {"evidence": None})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = "http://127.0.0.1:" + str(sock.getsockname()[1])
    app = FastAPI()
    web_ui.install(app, setup[1], origin + "/qbwc")
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    with sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(origin + "/app")
        page.get_by_label("Bridge access key").fill(TOKENS["operator-a"])
        page.get_by_role("button", name="Open workspace").click()
        page.get_by_label("Company", exact=True).select_option("company-a")
        page.get_by_role("heading", name="Documents", exact=True).wait_for()
        yield page
        browser.close()
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not errors


def test_browser_dispatch_rules_review_cancel_and_restart(page):
    page.get_by_role("button", name="Posting schedules", exact=True).click()
    page.get_by_label("Profile Id", exact=True).fill("browser-profile")
    page.get_by_label("Mode", exact=True).select_option("automatic")
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    assert page.get_by_role("button", name="Enable reviewed profile").is_disabled()
    page.get_by_role("button", name="Review dispatch rules").click()
    assert page.get_by_role("button", name="Enable reviewed profile").is_enabled()
    page.get_by_label("Max Amount Total", exact=True).fill("10.00")
    assert page.get_by_role("button", name="Enable reviewed profile").is_disabled()
    page.get_by_role("button", name="Review dispatch rules").click()
    page.get_by_role("button", name="Enable reviewed profile").click()
    page.get_by_text("browser-profile · automatic · Enabled", exact=True).wait_for()
    page.get_by_text("browser-profile · automatic · Enabled", exact=True).click()
    page.get_by_role("button", name="Cancel browser-profile", exact=True).click()
    page.get_by_text("browser-profile · automatic · Cancelled", exact=True).wait_for()
    page.get_by_role("button", name="Overview", exact=True).click()
    page.get_by_role("button", name="Posting schedules", exact=True).click()
    page.get_by_text("browser-profile · automatic · Cancelled", exact=True).wait_for()


def fill_invoice(page, reference="WEB-1"):
    page.get_by_role("button", name="New document", exact=True).first.click()
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_label("Customer", exact=True).select_option("customer-a")
    page.get_by_label("Reference", exact=True).fill(reference)
    page.get_by_label("Transaction date", exact=True).fill("2026-09-07")
    page.get_by_label("Item", exact=True).select_option("item-a")
    page.get_by_label("Unit price", exact=True).fill("5.00")


def test_browser_master_check_is_invalidated_by_field_edits(page, monkeypatch):
    observed = []

    def check(*args, **kwargs):
        observed.append(kwargs["payload"])
        return {"evidence": {"run": "123"}}

    monkeypatch.setattr(web_ui, "check_masters", check)
    page.get_by_role("button", name="Customers, suppliers & items", exact=True).click()
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_label("Reference", exact=True).fill("MASTER-1")
    page.get_by_label("Name", exact=True).fill("Synthetic Customer")
    page.get_by_label("Phone", exact=True).fill("555-0100")
    page.get_by_role("button", name="Check master details", exact=True).click()
    page.locator("body:not([aria-busy])").wait_for()
    assert observed[-1]["kind"] == "customer" and observed[-1]["fields"]["phone"] == "555-0100"
    assert page.get_by_role("button", name="Save master draft", exact=True).is_enabled()
    page.get_by_label("Phone", exact=True).fill("555-0101")
    assert page.get_by_role("button", name="Save master draft", exact=True).is_disabled()
    assert page.get_by_label("List Id", exact=True).is_disabled()


def test_browser_master_update_requires_exact_original_and_preserves_it(page, monkeypatch):
    from kaydbooks_bridge import master_checks

    original = {
        "ListID": "80000001-1234567890",
        "EditSequence": "123",
        "Name": "Synthetic Vendor",
        "IsActive": "true",
        "Phone": "555-0100",
        "Balance": "30.00",
    }
    target = {"list_id": original["ListID"], "edit_sequence": "123", "record_sha256": "a" * 64}
    monkeypatch.setattr(
        master_checks, "read", lambda *a, **kw: {"record": original, "target": target}
    )
    observed = []

    def check(*args, **kwargs):
        observed.append(kwargs["payload"])
        return {"evidence": {"run": "456"}}

    monkeypatch.setattr(web_ui, "check_masters", check)
    page.get_by_role("button", name="Customers, suppliers & items", exact=True).click()
    page.get_by_label("Kind", exact=True).select_option("supplier")
    page.get_by_label("Change Action", exact=True).select_option("update")
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_label("Reference", exact=True).fill("MASTER-2")
    page.get_by_label("List Id", exact=True).fill(original["ListID"])
    page.get_by_role("button", name="Read existing record", exact=True).click()
    page.locator("body:not([aria-busy])").wait_for()
    assert page.get_by_label("Phone", exact=True).input_value() == "555-0100"
    page.get_by_label("Phone", exact=True).fill("555-0101")
    page.get_by_role("button", name="Check master details", exact=True).click()
    page.locator("body:not([aria-busy])").wait_for()
    assert observed[-1]["target"] == target
    assert "balance" not in observed[-1]["fields"]
    page.get_by_label("List Id", exact=True).fill("80000002-1234567890")
    assert page.get_by_role("button", name="Save master draft", exact=True).is_disabled()


def test_browser_manual_review_correction_and_company_switch(page, setup):
    fill_invoice(page)
    page.get_by_role("button", name="Check details", exact=True).click()
    page.get_by_role("button", name="Save and review", exact=True).click()
    page.get_by_role("heading", name="WEB-1", exact=True).wait_for()
    assert page.get_by_text("Validated", exact=True).is_visible()
    assert len(setup[0].status(TOKENS["operator-a"], "company-a")["jobs"]) == 1
    page.get_by_role("button", name="Correct draft", exact=True).click()
    page.get_by_label("Quantity", exact=True).fill("2")
    page.get_by_label("Reason for correction", exact=True).fill("Correct quantity from source")
    page.get_by_role("button", name="Check details", exact=True).click()
    page.get_by_role("button", name="Save correction and review", exact=True).click()
    page.get_by_role("heading", name="WEB-1", exact=True).wait_for()
    states = [j["state"] for j in setup[0].status(TOKENS["operator-a"], "company-a")["jobs"]]
    assert states == ["superseded", "validated"]
    page.get_by_label("Company", exact=True).select_option("")
    page.get_by_role("heading", name="Choose your company").wait_for()
    assert not page.get_by_text("WEB-1", exact=True).count()


def test_browser_edit_invalidates_check_and_preserves_exact_cents(page):
    fill_invoice(page)
    page.get_by_role("button", name="Check details", exact=True).click()
    page.locator("body:not([aria-busy])").wait_for()
    page.get_by_label("Unit price", exact=True).fill("5.125")
    assert page.get_by_label("Amount", exact=True).input_value() == "5.13"
    assert page.get_by_role("button", name="Save and review", exact=True).is_disabled()
    page.get_by_label("Unit price", exact=True).fill("1e3")
    assert page.get_by_label("Amount", exact=True).input_value() == ""


def test_browser_access_revocation_is_live(page, setup):
    page.get_by_role("button", name="User access", exact=True).click()
    page.get_by_role("heading", name="Company access", exact=True).wait_for()
    page.get_by_label("User", exact=True).fill("preparer-a")
    page.get_by_role("button", name="Revoke company access", exact=True).click()
    page.get_by_text("Company access revoked.", exact=True).wait_for()
    raw = json.loads(setup[1].read_text())
    assert raw["principals"]["preparer-a"]["companies"]["company-a"] == []
    assert raw["principals"]["operator-b"]["companies"]["company-b"]


def test_browser_report_dates_labels_and_untrusted_cells(page, monkeypatch):
    from kaydbooks_bridge import reports

    def native(bridge, token, company, connector_id, run_id, specification):
        assert company == "company-a" and connector_id == "connector-company-a"
        assert specification == {
            "report": "profit-loss",
            "date_from": "2026-09-01",
            "date_to": "2026-09-07",
            "basis": "Accrual",
        }
        return {
            "report": {
                "title": "Profit & Loss",
                "subtitle": "September 1 - 7, 2026",
                "basis": "Accrual",
                "row_count": 1,
                "columns": [
                    {"id": 1, "titles": [{"value": "Account"}]},
                    {"id": 2, "titles": [{"value": "Total"}]},
                ],
                "rows": [
                    {
                        "kind": "TotalRow",
                        "label": {"value": "<img src=x onerror=alert(1)>"},
                        "text": None,
                        "cells": {"2": {"value": "7.00"}},
                    }
                ],
                "read_started_at": 1788740000,
                "response_sha256": "f" * 64,
            }
        }

    monkeypatch.setattr(reports, "native", native)
    page.get_by_role("button", name="Reports", exact=True).click()
    page.get_by_label("Date From", exact=True).fill("2026-09-01")
    page.get_by_label("Date To", exact=True).fill("2026-09-07")
    page.get_by_role("button", name="Run report", exact=True).click()
    page.locator("#report-result table").wait_for()
    assert page.locator("#report-result tbody").inner_text() == "<img src=x onerror=alert(1)>\t7.00"
    assert page.locator("#report-result img").count() == 0


@pytest.mark.skipif(os.getenv("KAYDBOOKS_OCR_TESTS") != "1", reason="explicit local OCR runtime")
def test_browser_upload_observations_and_required_field_review(page, setup):
    from pathlib import Path

    page.get_by_role("button", name="Upload document", exact=True).click()
    page.get_by_label("PDF, photo or scan", exact=True).set_input_files(
        str(Path(__file__).with_name("fixtures") / "intake/clean-scan.png")
    )
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_role("button", name="Extract for review", exact=True).click()
    page.get_by_text(
        "Retained source observations - review against the original", exact=True
    ).wait_for(timeout=45000)
    page.get_by_label("Customer", exact=True).select_option("customer-a")
    page.get_by_label("Reference", exact=True).fill("SCAN-1")
    page.get_by_label("Transaction date", exact=True).fill("2026-09-07")
    page.get_by_label("Item", exact=True).select_option("item-a")
    page.get_by_label("Quantity", exact=True).fill("2")
    page.get_by_label("Unit price", exact=True).fill("5.00")
    page.get_by_role("button", name="Check details", exact=True).click()
    page.get_by_role("button", name="Save and review", exact=True).click()
    page.get_by_role("heading", name="SCAN-1", exact=True).wait_for()
    assert page.get_by_text("Draft", exact=True).is_visible()
    page.get_by_role("button", name="Validate draft", exact=True).click()
    page.get_by_text(
        "uncertain extracted fields require explicit source review", exact=True
    ).wait_for()
    for checkbox in page.locator("#content input[type=checkbox]").all():
        checkbox.check()
    page.get_by_role("button", name="Confirm reviewed values", exact=True).click()
    page.get_by_text(
        "Source review recorded. The draft can now be validated.", exact=True
    ).wait_for()
    page.get_by_role("button", name="Validate draft", exact=True).click()
    page.get_by_text("Validated", exact=True).wait_for()
    assert len(setup[0].status(TOKENS["operator-a"], "company-a")["jobs"]) == 1


def test_browser_spreadsheet_mapping_errors_and_retry(page, setup):
    page.get_by_role("button", name="Import spreadsheet", exact=True).click()
    page.get_by_label("CSV or XLSX file").set_input_files(
        {
            "name": "rows.csv",
            "mimeType": "text/csv",
            "buffer": b"Key,Reference,Amount\none,WEB-IMP-1,5.00\ntwo,WEB-IMP-2,5.005\n",
        }
    )
    page.get_by_label("Dataset name (keep the same for re-imports)").fill("browser-intake")
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_role("button", name="Read columns", exact=True).click()
    page.get_by_role("heading", name="Set spreadsheet defaults").wait_for()
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    page.get_by_label("Customer", exact=True).select_option("customer-a")
    page.get_by_label("Reference", exact=True).fill("DEFAULT-1")
    page.get_by_label("Transaction date", exact=True).fill("2026-09-07")
    page.get_by_label("Item", exact=True).select_option("item-a")
    page.get_by_label("Unit price", exact=True).fill("5.00")
    page.get_by_role("button", name="Use these defaults for the spreadsheet").click()
    page.get_by_label("Stable row identity", exact=True).select_option("Key")
    page.get_by_label("Reference", exact=True).select_option("Reference")
    page.get_by_label("Lines / Line 1 / Amount", exact=True).select_option("Amount")
    page.get_by_role("button", name="Preview rows", exact=True).click()
    page.get_by_role("heading", name="Review import", exact=True).wait_for()
    assert page.get_by_label("Select row 3").is_disabled()
    page.get_by_role("button", name="Select valid rows", exact=True).click()
    page.get_by_role("button", name="Prepare selected drafts", exact=True).click()
    page.get_by_text("Draft prepared", exact=True).wait_for()
    page.get_by_role("button", name="Prepare selected drafts", exact=True).click()
    page.get_by_text("Selected rows processed.", exact=False).wait_for()
    assert len(setup[0].status(TOKENS["operator-a"], "company-a")["jobs"]) == 1


@pytest.mark.parametrize("operation", [op for op in web_ui.OPERATIONS if op != "master.change"])
def test_browser_all_operation_forms_use_exact_shared_payload_fields(page, operation):
    page.get_by_role("button", name="New document", exact=True).first.click()
    page.get_by_label("Document type", exact=True).select_option(operation)
    form = page.locator("#document-form")
    for name, value in {
        "customer_id": "customer-a",
        "vendor_id": "vendor-a",
        "currency": "USD",
        "deposit_id": "cash",
        "bank_id": "cash",
        "method_id": "cash",
        "expense_id": "office",
        "item_id": "item-a",
    }.items():
        for element in form.locator(f'select[data-field="{name}"]').all():
            element.select_option(value)
    for name, value in {
        "ref_number": "FORM-1",
        "txn_date": "2026-09-07",
        "due_date": "2026-10-07",
        "invoice_txn_id": "INV-1",
        "bill_txn_id": "BILL-1",
        "credit_txn_id": "CR-1",
        "txn_id": "TXN-1",
        "total_amount": "5.00",
        "quantity": "1",
        "unit_price": "5.00",
        "amount": "5.00",
    }.items():
        for element in form.locator(f'input[data-field="{name}"]:not([readonly])').all():
            element.fill(value)
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    with page.expect_request(
        lambda r: r.method == "POST" and r.post_data_json.get("action") == "check"
    ) as captured:
        page.get_by_role("button", name="Check details", exact=True).click()
    request = captured.value.post_data_json
    assert request["company"] == "company-a" and request["parameters"]["operation"] == operation
    payload = request["parameters"]["payload"]
    assert payload["ref_number"] == "FORM-1" and payload["currency"] == "USD"
    if operation.endswith(".apply"):
        assert "txn_date" not in payload and "lines" not in payload and "allocations" not in payload
        assert payload["credit_txn_id"] == "CR-1"
    elif "payment" in operation or operation == "customer-refund.create":
        assert payload["allocations"] == [{"txn_id": "TXN-1", "amount": "5.00"}]
    else:
        assert payload["lines"][0]["amount"] == "5.00"


@pytest.mark.parametrize("kind", ["customer", "supplier"])
def test_browser_explicit_payment_discount_fields(page, monkeypatch, kind):
    observed = []

    def check(*args, **kwargs):
        observed.append(kwargs["payload"])
        return {"evidence": None}

    monkeypatch.setattr(web_ui, "check_masters", check)
    page.get_by_role("button", name="New document", exact=True).first.click()
    page.get_by_label("Document type", exact=True).select_option(kind + "-payment.create")
    page.get_by_label("Source", exact=True).select_option("synthetic-intake")
    form = page.locator("#document-form")
    if kind == "supplier":
        assert form.locator('input[data-field="ref_number"]').get_attribute("maxlength") == "11"
    for name, value in {
        "customer_id": "customer-a",
        "vendor_id": "vendor-a",
        "currency": "USD",
        "deposit_id": "cash",
        "bank_id": "cash",
        "method_id": "cash",
        "discount_account": kind + "_discount",
    }.items():
        for element in form.locator(f'select[data-field="{name}"]').all():
            element.select_option(value)
    for name, value in {
        "ref_number": "DISC-1",
        "txn_date": "2026-09-07",
        "txn_id": "TXN-1",
        "total_amount": "5",
        "amount": "5",
        "discount_amount": "1",
    }.items():
        form.locator(f'input[data-field="{name}"]').fill(value)
    page.get_by_role("button", name="Check details", exact=True).click()
    page.locator("body:not([aria-busy])").wait_for()
    assert observed[-1]["total_amount"] == "5.00"
    assert observed[-1]["allocations"] == [
        {
            "txn_id": "TXN-1",
            "amount": "5.00",
            "discount_amount": "1.00",
            "discount_account": kind + "_discount",
        }
    ]
    form.locator('input[data-field="discount_amount"]').fill("2")
    assert page.get_by_role("button", name="Save and review", exact=True).is_disabled()
