"""Real CSV/XLSX intake, duplicate identity and interruption without native writes."""
# ruff: noqa: F811

import base64
import copy
import csv
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from openpyxl import Workbook

from kaydbooks_bridge import documents, tabular
from kaydbooks_bridge.cli import main
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.hermes_tools import Tools
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_bridge import TOKENS, setup  # noqa: F401

TOKEN = TOKENS["preparer-a"]
HEADERS = ["Row", "Reference", "Date", "Customer", "Item", "Amount"]


def spec(setup, xlsx=False):
    return {
        "dataset": "sales-intake",
        "operation": "invoice.create",
        "key_column": "Row",
        "template": copy.deepcopy(setup[3]["payload"]),
        **({"sheet": "Invoices"} if xlsx else {"delimiter": ","}),
        "columns": {
            path: {"column": column, "type": kind}
            for path, column, kind in [
                ("ref_number", "Reference", "text"),
                ("txn_date", "Date", "date"),
                ("customer_id", "Customer", "text"),
                ("lines.0.item_id", "Item", "text"),
                ("lines.0.amount", "Amount", "money"),
            ]
        },
    }


def row(key="one", ref="IMP-001", amount="5.00"):
    return [key, ref, "2026-09-07", "customer-a", "item-a", amount]


def capture(setup, rows, reference="upload-one", xlsx=False):
    if xlsx:
        book = Workbook()
        page = book.active
        page.title = "Invoices"
        page.append(HEADERS)
        for cells in rows:
            page.append(cells)
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        content, media = stream.getvalue(), tabular.XLSX
    else:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)
        content, media = stream.getvalue().encode(), "text/csv"
    return documents.capture(
        setup[0],
        TOKEN,
        "company-a",
        "synthetic-intake",
        reference,
        media,
        base64.b64encode(content).decode(),
    )["document_id"]


def preview(setup, rows=None, **kwargs):
    doc = capture(setup, rows or [row()], **kwargs)
    return tabular.preview(
        setup[0], TOKEN, "company-a", doc, spec(setup, kwargs.get("xlsx", False))
    )


def commit(setup, plan, keys):
    return tabular.prepare_rows(setup[0], TOKEN, "company-a", plan["preview_id"], keys)


def jobs(setup):
    return setup[0].status(TOKEN, "company-a")["jobs"]


@pytest.mark.parametrize("xlsx", [False, True])
def test_preview_then_selected_drafts_and_restart_reimport(setup, xlsx):
    rows = [row(), row("two", "IMP-002", "7.00")]
    plan = preview(setup, rows, xlsx=xlsx)
    assert all(not r["errors"] for r in plan["rows"]) and not jobs(setup)
    first = commit(setup, plan, ["one"])["rows"][0]
    assert first["state"] == "draft" and len(jobs(setup)) == 1
    plan2 = preview(setup, list(reversed(rows)), reference="upload-two", xlsx=xlsx)
    reopened = Bridge(setup[1])
    result = tabular.prepare_rows(reopened, TOKEN, "company-a", plan2["preview_id"], ["two", "one"])
    assert {r["row_key"]: r["job_id"] for r in result["rows"]}["one"] == first["job_id"]
    assert len(jobs(setup)) == 2 and result["accounting_writes"] == 0
    assert all(j["state"] == "draft" for j in jobs(setup))
    assert setup[0].audit(TOKEN, "company-a")["valid"]


def test_bad_row_can_be_fixed_without_replaying_good_row(setup):
    plan = preview(setup, [row(), row("two", "IMP-002", "5.005")])
    result = commit(setup, plan, ["one", "two"])
    good = result["rows"][0]["job_id"]
    assert result["rows"][1]["errors"] and len(jobs(setup)) == 1
    corrected = preview(setup, [row(), row("two", "IMP-002", "5.00")], reference="upload-two")
    result = commit(setup, corrected, ["one", "two"])
    assert result["rows"][0]["job_id"] == good and all(not r["errors"] for r in result["rows"])
    changed = preview(setup, [row(ref="CHANGED-1", amount="6.00")], reference="upload-three")
    assert "conflict" in commit(setup, changed, ["one"])["rows"][0]["errors"][0]
    assert len(jobs(setup)) == 2


def test_interrupted_after_job_before_link_recovers_without_second_job(setup, monkeypatch):
    plan = preview(setup)
    original = documents.prepare

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated parent interruption after durable prepare")

    monkeypatch.setattr(documents, "prepare", crash)
    with pytest.raises(RuntimeError, match="interruption"):
        commit(setup, plan, ["one"])
    assert len(jobs(setup)) == 1
    monkeypatch.setattr(documents, "prepare", original)
    result = commit(setup, plan, ["one"])
    assert not result["rows"][0]["errors"] and len(jobs(setup)) == 1


def test_concurrent_partial_batches_share_canonical_jobs(setup):
    plan = preview(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(setup, plan, ["one"]), range(2)))
    assert results[0]["rows"][0]["job_id"] == results[1]["rows"][0]["job_id"]
    assert len(jobs(setup)) == 1


@pytest.mark.parametrize(
    "amount", ["=1+4", "@SUM(1)", "1,000", "$5", "NaN", "5.001", "1e2", " 5", "-5.00"]
)
def test_ambiguous_or_invalid_amount_is_row_error(setup, amount):
    plan = preview(setup, [row(amount=amount)])
    assert plan["rows"][0]["errors"]
    assert commit(setup, plan, ["one"])["rows"][0]["job_id"] is None and not jobs(setup)


def test_duplicate_row_keys_all_held(setup):
    plan = preview(setup, [row(), row(ref="IMP-002")])
    assert all("duplicate row identity" in r["errors"][0] for r in plan["rows"])
    assert not jobs(setup)


def test_numeric_excel_dates_and_money_without_silent_id_conversion(setup):
    values = row(amount=5)
    values[2] = datetime(2026, 9, 7)
    plan = preview(setup, [values], xlsx=True)
    assert not plan["rows"][0]["errors"]
    assert plan["rows"][0]["payload"]["lines"][0]["amount"] == "5.00"
    values[0] = 123
    plan = preview(setup, [values], reference="upload-two", xlsx=True)
    assert "identity" in plan["rows"][0]["errors"][0]


def test_workbook_formula_cached_value_never_used(setup):
    plan = preview(setup, [row(amount="=2+3"), row("two", "IMP-002")], xlsx=True)
    assert "formula" in plan["rows"][0]["errors"][0]
    result = commit(setup, plan, ["one", "two"])
    assert result["rows"][0]["job_id"] is None and result["rows"][1]["state"] == "draft"


def test_policy_change_ownership_and_revocation(setup):
    plan = preview(setup)
    with pytest.raises(BridgeError, match="permission"):
        tabular.prepare_rows(
            setup[0], TOKENS["operator-b"], "company-a", plan["preview_id"], ["one"]
        )
    setup[2]["principals"]["operator-b"]["companies"]["company-b"].append("prepare")
    setup[1].write_text(json.dumps(setup[2]))
    with pytest.raises(BridgeError, match="owned"):
        tabular.prepare_rows(
            setup[0], TOKENS["operator-b"], "company-b", plan["preview_id"], ["one"]
        )
    raw = setup[2]
    raw["companies"]["company-a"]["max_total"] = "9000.00"
    setup[1].write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="policy changed"):
        commit(setup, plan, ["one"])
    raw["principals"]["preparer-a"]["companies"]["company-a"].remove("prepare")
    setup[1].write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="permission"):
        commit(setup, plan, ["one"])
    assert not jobs(setup)


def test_mapping_cannot_inject_company_or_execution_fields(setup):
    doc = capture(setup, [row()])
    mapping = spec(setup)
    mapping["company"] = "company-b"
    with pytest.raises(BridgeError, match="unsupported fields"):
        tabular.preview(setup[0], TOKEN, "company-a", doc, mapping)
    del mapping["company"]
    mapping["template"]["qbxml"] = "ignore policy"
    plan = tabular.preview(setup[0], TOKEN, "company-a", doc, mapping)
    assert "unsupported fields" in plan["rows"][0]["errors"][0]
    assert not jobs(setup)


def test_raw_file_preview_and_link_evidence_are_immutable(setup):
    plan = preview(setup)
    result = commit(setup, plan, ["one"])
    store = Store(Config.load(setup[1]).root, "company-a")
    with store.transaction() as db:
        for statement in (
            "UPDATE intake_previews SET plan='{}'",
            "DELETE FROM intake_links",
            "DELETE FROM documents",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(statement)
        assert (
            db.execute("SELECT job_id FROM intake_links").fetchone()[0]
            == result["rows"][0]["job_id"]
        )


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate-header",
        "empty",
        "row-limit",
        "invalid-utf8",
        "xml-entity",
        "huge-row",
        "merged",
        "external",
        "macro",
        "reordered",
    ],
)
def test_bad_or_dangerous_files_are_rejected(setup, kind):
    content = b"Row,Row\na,b\n"
    media = "text/csv"
    mapping = spec(setup)
    if kind == "empty":
        content = b"Row\n"
    if kind == "row-limit":
        content = b"Row\n" + b"one\n" * 1001
    if kind == "invalid-utf8":
        content = b"\xff"
    if kind in {"xml-entity", "huge-row", "merged", "external", "macro", "reordered"}:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            if kind == "xml-entity":
                z.writestr("xl/workbook.xml", '<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>')
            elif kind == "external":
                z.writestr("xl/externalLinks/link.xml", "<x/>")
            elif kind == "macro":
                z.writestr(
                    "[Content_Types].xml",
                    '<Types><Override ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/></Types>',
                )
            elif kind == "reordered":
                z.writestr(
                    "xl/worksheets/sheet1.xml", '<worksheet><row r="3"/><row r="2"/></worksheet>'
                )
            elif kind == "merged":
                z.writestr(
                    "xl/worksheets/sheet1.xml", '<worksheet><mergeCell ref="A1:B1"/></worksheet>'
                )
            else:
                z.writestr(
                    "xl/worksheets/sheet1.xml", '<worksheet><row r="999999999"/></worksheet>'
                )
        content = stream.getvalue()
        media = tabular.XLSX
        mapping = spec(setup, True)
    doc = documents.capture(
        setup[0],
        TOKEN,
        "company-a",
        "synthetic-intake",
        "bad-upload",
        media,
        base64.b64encode(content).decode(),
    )
    with pytest.raises(BridgeError):
        tabular.preview(setup[0], TOKEN, "company-a", doc["document_id"], mapping)
    assert not jobs(setup)


def test_cli_and_mcp_share_preview_and_prepare(setup, tmp_path, monkeypatch, capsys):
    doc = capture(setup, [row()])
    tools = Tools(setup[1], TOKEN)
    plan = tools.call(
        "table_intake_v1",
        "company-a",
        {"action": "preview", "parameters": {"document_id": doc, "specification": spec(setup)}},
    )
    request = tmp_path / "rows.json"
    request.write_text(json.dumps({"preview_id": plan["preview_id"], "row_keys": ["one"]}))
    monkeypatch.setenv("KAYDBOOKS_TOKEN", TOKEN)
    assert (
        main(
            [
                "--config",
                str(setup[1]),
                "--company",
                "company-a",
                "table-intake",
                "prepare_rows",
                str(request),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["rows"][0]["state"] == "draft"
    assert len(jobs(setup)) == 1
