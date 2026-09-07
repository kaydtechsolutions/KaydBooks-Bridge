"""Native report framing, completeness, read recovery and immutable source time."""
# ruff: noqa: F811

import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge import native_reports as reports
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import (  # noqa: F401
    COMPANY_A,
    COMPANY_B,
    HOST,
    PASSWORD_A,
    discovery_setup,
)


@pytest.fixture
def case(direct):
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"].append("report")
    path.write_text(json.dumps(raw))
    return path, token


def specification(name="profit-loss"):
    result = {"report": name, "date_to": "2026-09-07", "basis": "Accrual"}
    if reports.REPORTS[name][2] == "period":
        result["date_from"] = "2026-01-01"
    if name.endswith("statement"):
        result["entity_list_id"] = "entity-id"
    return result


def response(request, *, company=COMPANY_A, alter=None):
    query = ET.fromstring(request)[0][-1]
    run = query.attrib["requestID"][:-1]
    root = ET.fromstring(
        FakeQuickBooks(
            entities={"Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}], "Company": [company]}
        )(S._discovery_request(run, "17.0"))
    )
    pref = ET.SubElement(
        root[0], "PreferencesQueryRs", requestID=run + "3", statusCode="0", statusSeverity="Info"
    )
    ET.SubElement(
        ET.SubElement(ET.SubElement(pref, "PreferencesRet"), "MultiCurrencyPreferences"),
        "IsMultiCurrencyOn",
    ).text = "false"
    answer = ET.SubElement(
        root[0],
        query.tag.replace("Rq", "Rs"),
        requestID=run + "4",
        statusCode="0",
        statusSeverity="Info",
    )
    ret = ET.SubElement(answer, "ReportRet")
    for k, v in {
        "ReportTitle": "Synthetic Report",
        "ReportSubtitle": "As of September 7, 2026",
        "ReportBasis": "Accrual",
        "NumRows": "2",
        "NumColumns": "2",
        "NumColTitleRows": "1",
    }.items():
        ET.SubElement(ret, k).text = v
    for number, kind, dtype in [("1", "Name", "STRTYPE"), ("2", "Amount", "AMTTYPE")]:
        col = ET.SubElement(ret, "ColDesc", colID=number, dataType=dtype)
        ET.SubElement(col, "ColTitle", titleRow="1", value=kind)
        ET.SubElement(col, "ColType").text = kind
    data = ET.SubElement(ret, "ReportData")
    for number, kind, label in [("1", "DataRow", "Revenue"), ("2", "TotalRow", "TOTAL")]:
        row = ET.SubElement(data, kind, rowNumber=number)
        ET.SubElement(row, "ColData", colID="1", value=label)
        ET.SubElement(row, "ColData", colID="2", value="25.00")
    if alter:
        alter(root)
    return ET.tostring(root, encoding="unicode")


def run(case, spec=None, *, alter=None, exchange=None, **kwargs):
    path, token = case

    def transport(request, path):
        path.write_text(response(request, alter=alter))

    return discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        report_query=spec or specification(),
        exchange=exchange or transport,
        **kwargs,
    )


@pytest.mark.parametrize("name", list(reports.REPORTS))
def test_supported_fixed_report_requests_preserve_native_totals(case, name):
    result = run(case, specification(name))
    report = result["report"]
    assert report["complete"] and report["row_count"] == 2 and report["currency"] == "USD"
    assert report["native_totals"][0]["cells"]["2"]["decimal"] == "25.00"
    assert report["read_started_at"] and report["derived"] is False
    assert report["source"]["run"] == "1234" and result["live_posting"] is False


@pytest.mark.parametrize(
    "error",
    [
        "missing-row",
        "duplicate-row",
        "missing-column",
        "duplicate-cell",
        "basis",
        "warning",
        "multicurrency",
        "wrong-id",
        "wrong-company",
        "wrong-date",
    ],
)
def test_report_rejects_incomplete_mismatched_or_unsupported_evidence(case, error):
    def alter(root):
        ret = root.find(".//ReportRet")
        data = ret.find("ReportData")
        if error == "missing-row":
            data.remove(data[0])
        if error == "duplicate-row":
            data[1].set("rowNumber", "1")
        if error == "missing-column":
            ret.remove(ret.find("ColDesc"))
        if error == "duplicate-cell":
            data[0].append(ET.fromstring(ET.tostring(data[0][0])))
        if error == "wrong-date":
            ret.find("ReportSubtitle").text = "As of September 6, 2026"
        if error == "basis":
            ret.find("ReportBasis").text = "Cash"
        if error == "warning":
            root[0][-1].set("statusCode", "1")
        if error == "multicurrency":
            root.find(".//IsMultiCurrencyOn").text = "true"
        if error == "wrong-id":
            root[0][-1].set("requestID", "other")
        if error == "wrong-company":
            root.find(".//CompanyName").text = COMPANY_B["CompanyName"]

    with pytest.raises(BridgeError, match="validation"):
        run(case, alter=alter)
    with pytest.raises(BridgeError, match="blocked"):
        run(case)


def test_empty_native_report_is_complete_without_inventing_zero(case):
    def alter(root):
        root.find(".//NumRows").text = "0"
        root.find(".//ReportData").clear()

    report = run(case, alter=alter)["report"]
    assert report["rows"] == [] and report["native_totals"] == [] and report["complete"]


def test_restart_retains_report_time_and_does_not_query_again(case):
    def interrupted(request, path):
        path.write_text(response(request))
        raise RuntimeError("parent exit")

    with pytest.raises(RuntimeError):
        run(case, exchange=interrupted)
    first = run(case, exchange=lambda *_: pytest.fail("saved response must be used"))
    second = run(case, exchange=lambda *_: pytest.fail("verified read must not be repeated"))
    assert first == second


def test_report_permission_and_policy_before_io(case):
    path, token = case
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"].remove("report")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="permission"):
        run(case, exchange=lambda *_: pytest.fail("unauthorized IO"))


@pytest.mark.parametrize(
    "change",
    [
        {"report": "sales-tax"},
        {"basis": "None"},
        {"date_from": "not-a-date"},
        {"date_to": "2025-01-01"},
        {"qbxml": "<InvoiceAddRq/>"},
        {"entity_list_id": "bad id"},
        {"columns_by": "SQL"},
    ],
)
def test_invalid_report_parameters_fail_before_io(case, change):
    with pytest.raises(BridgeError):
        run(case, {**specification(), **change}, exchange=lambda *_: pytest.fail("invalid IO"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows compiler")
def test_compiled_report_allowlist_matches_builder_and_rejects_write(case, tmp_path):
    source = (Path(__file__).parents[1] / "src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    methods = source[
        source.index(" static void FixedQuery(") : source.index(" public static void Run(")
    ]
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System; public class Gate {\n"
        + methods
        + "}\n'@\n"
        + """foreach($file in Get-ChildItem -LiteralPath $args[0] -Filter '*.xml') {
      $doc=New-Object System.Xml.XmlDocument; $doc.Load($file.FullName)
      [Gate]::ReportQuery($doc.DocumentElement,'1234')
      foreach($bad in @($doc.OuterXml.Replace('DisplayReport>false','DisplayReport>true'),$doc.OuterXml.Replace('ReportQueryRq','ReportAddRq'),$doc.OuterXml.Replace('</ReportPeriod>','</ReportPeriod><ReportDateMacro>All</ReportDateMacro>'))) {
       $rejected=$false
       try { $other=New-Object System.Xml.XmlDocument; $other.LoadXml($bad); [Gate]::ReportQuery($other.DocumentElement,'1234') } catch {$rejected=$true}
       if(-not $rejected){throw 'unsafe report accepted'}
      }
    }
    Write-Output 'Report gate passed'
    """,
        encoding="utf-8",
    )
    policy = Config.load(case[0]).companies["company-a"]
    for name in reports.REPORTS:
        request = reports.append_queries(
            S._discovery_request("1234", "17.0"), "1234", reports.plan(policy, specification(name))
        )
        (tmp_path / (name + ".xml")).write_text(
            ET.tostring(ET.fromstring(request)[0][-1], encoding="unicode")
        )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert "Report gate passed" in result.stdout
