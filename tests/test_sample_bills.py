"""Controlled bill writes use a synthetic native session in these tests."""

# ruff: noqa: F811
import json
import os
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.sample_bill_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_bill_lookup import exact_case, exact_response, exact_run  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401


@pytest.fixture
def queued_bill(exact_case):
    path, token, payload = exact_case
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] += [
        "post-sample",
        "recover",
        "report",
        "pause",
    ]
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_bill_posting={
            "connector": "connector-company-a",
            "authorization": "Controlled synthetic bill test authorization",
            "ref_prefix": "BILL-",
            "max_bills": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    exact_run(exact_case)
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        operation="bill.create",
        payload=payload,
        master_evidence={
            "transport": "direct-sdk",
            "connector": "connector-company-a",
            "id": "993",
        },
    )
    envelope["source"]["namespace"] = raw["companies"]["company-a"]["sources"][0]
    bridge = Bridge(path)
    job = bridge.prepare(token, "company-a", envelope)
    bridge.action(token, "company-a", job["id"], "validate")
    bridge.action(token, "company-a", job["id"], "submit")
    return bridge, token, job["id"], payload


def saved_bill(request_id, *, operation="BillQuery", vendor="V-A"):
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRs")
    row = ET.SubElement(
        ET.SubElement(
            batch, operation + "Rs", requestID=request_id, statusCode="0", statusSeverity="Info"
        ),
        "BillRet",
    )
    for name, value in {
        "TxnID": "saved-bill",
        "EditSequence": "1234",
        "TxnDate": "2026-09-06",
        "DueDate": "2026-10-06",
        "RefNumber": "BILL-001",
        "AmountDue": "10.00",
        "OpenAmount": "10.00",
        "IsPaid": "false",
    }.items():
        ET.SubElement(row, name).text = value
    for name, value in (("VendorRef", vendor), ("APAccountRef", "AP-A")):
        ET.SubElement(ET.SubElement(row, name), "ListID").text = value
    line = ET.SubElement(row, "ExpenseLineRet")
    ET.SubElement(line, "TxnLineID").text = "bill-line"
    ET.SubElement(ET.SubElement(line, "AccountRef"), "ListID").text = "E-A"
    ET.SubElement(line, "Amount").text = "10.00"
    ET.SubElement(line, "BillableStatus").text = "NotBillable"
    return root


def receipt_exchange(request, dest):
    from test_direct_sdk import transport

    root = ET.fromstring(request)
    query = root[0][-1]
    root[0].remove(query)
    transport()(ET.tostring(root, encoding="unicode"), dest)
    result = ET.fromstring(dest.read_text())
    result[0].append(saved_bill(query.get("requestID"))[0][0])
    dest.write_text(ET.tostring(result, encoding="unicode"))


class Session:
    def __init__(self, existing=False, crash=None, before=None, other_vendor=False):
        self.existing, self.crash, self.before, self.other_vendor = (
            existing,
            crash,
            before,
            other_vendor,
        )
        self.writes = 0

    def __call__(self, request, write, folder, approve):
        root = ET.fromstring(request)
        query = root[0][-1]
        assert query.tag == "BillQueryRq"
        root[0].remove(query)
        result = ET.fromstring(exact_response(ET.tostring(root, encoding="unicode")))
        if self.existing or self.other_vendor:
            rs = saved_bill(query.get("requestID"), vendor="V-A" if self.existing else "OTHER")[0][
                0
            ]
        else:
            rs = ET.Element(
                "BillQueryRs",
                requestID=query.get("requestID"),
                statusCode="500",
                statusSeverity="Warn",
            )
        result[0].append(rs)
        if self.before:
            self.before()
        if not approve(ET.tostring(result, encoding="unicode")) or write is None:
            return None
        if self.crash == "before":
            raise RuntimeError("before write")
        self.writes += 1
        self.existing = True
        if self.crash == "after":
            raise RuntimeError("response lost")
        return ET.tostring(
            saved_bill(ET.fromstring(write)[0][0].get("requestID"), operation="BillAdd"),
            encoding="unicode",
        )


@pytest.mark.parametrize(
    "existing,other,count", [(False, False, 1), (True, False, 0), (False, True, 1)]
)
def test_bill_native_lifecycle_and_vendor_scoped_duplicate(queued_bill, existing, other, count):
    bridge, token, job_id, _ = queued_bill
    session = Session(existing=existing, other_vendor=other)
    result = post(
        bridge, token, "company-a", job_id, exchange=session, read_exchange=receipt_exchange
    )
    assert result["state"] == "verified" and session.writes == count
    assert result["transaction_receipt"]["receipt"]["operation"] == "bill.create"
    with pytest.raises(BridgeError, match="never retry"):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert bridge.audit(token, "company-a")["valid"]
    from kaydbooks_bridge.reports import register

    assert register(bridge, token, "company-a", "2026-01-01", "2026-12-31")["rows"] == []


def test_bill_unknown_reconciles_without_resend(queued_bill):
    bridge, token, job_id, _ = queued_bill
    session = Session(crash="after")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    with pytest.raises(BridgeError):
        bridge.reconcile(token, "company-a", job_id)
    result = reconcile(
        Bridge(bridge.config_path),
        token,
        "company-a",
        job_id,
        exchange=session,
        read_exchange=receipt_exchange,
    )
    assert result["state"] == "verified" and session.writes == 1


def test_bill_missing_after_crash_is_held(queued_bill):
    bridge, token, job_id, _ = queued_bill
    session = Session(crash="before")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    with pytest.raises(BridgeError, match="inconclusive"):
        reconcile(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0


def test_explicit_3210_plus_fresh_absence_ends_attempt_without_retry(queued_bill):
    bridge, token, job_id, _ = queued_bill

    class Rejected(Session):
        def __call__(self, request, write, folder, approve):
            if write is None:
                return super().__call__(request, write, folder, approve)
            self.crash = "before"
            with suppress(RuntimeError):
                super().__call__(request, write, folder, approve)
            root = ET.Element("QBXML")
            ET.SubElement(
                ET.SubElement(root, "QBXMLMsgsRs"),
                "BillAddRs",
                requestID=ET.fromstring(write)[0][0].get("requestID"),
                statusCode="3210",
                statusSeverity="Error",
            )
            result = ET.tostring(root, encoding="unicode")
            folder.mkdir()
            (folder / "add.response.xml").write_text(result)
            return result

    session = Rejected()
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    result = reconcile(bridge, token, "company-a", job_id, exchange=session)
    assert result["state"] == "failed" and session.writes == 0
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert bridge.audit(token, "company-a")["valid"]


def test_bill_document_tool_retains_source_and_never_posts(exact_case):
    import base64

    from kaydbooks_bridge.documents import fields
    from kaydbooks_bridge.hermes_tools import Tools

    path, token, payload = exact_case
    tools = Tools(path, token)
    config = Config.load(path)
    doc = tools.call(
        "capture_document_v1",
        "company-a",
        {
            "namespace": config.companies["company-a"].sources[0],
            "reference": "bill-doc",
            "media_type": "text/plain",
            "content_base64": base64.b64encode(b"Synthetic supplier bill: ten dollars").decode(),
        },
    )
    job = tools.call(
        "prepare_bill_v1",
        "company-a",
        {
            "document_id": doc["document_id"],
            "idempotency_key": "bill-doc",
            "payload": payload,
            "confidence": {key: 1 for key in fields(payload)},
        },
    )
    assert job["operation"] == "bill.create" and job["state"] == "draft" and job["attempt"] is None


@pytest.mark.parametrize("change", ["pause", "permission", "mapping", "expiry"])
def test_bill_authority_rechecked_before_write(queued_bill, change):
    bridge, token, job_id, _ = queued_bill

    def before():
        path = Path(bridge.config_path)
        raw = json.loads(path.read_text())
        if change == "pause":
            bridge.pause(token, "company-a", True)
        elif change == "permission":
            raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                "post-sample"
            )
        elif change == "mapping":
            raw["companies"]["company-a"]["bill_masters"]["payable"] = "CHANGED"
        else:
            raw["companies"]["company-a"]["sample_bill_posting"]["expires_at"] = 1
        path.write_text(json.dumps(raw))

    session = Session(before=before)
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0


@pytest.mark.parametrize(
    "old,new",
    [
        ("AmountDue>10.00", "AmountDue>11.00"),
        ("V-A", "WRONG"),
        ("IsPaid>false", "IsPaid>true"),
        ("OpenAmount>10.00", "OpenAmount>1.00"),
        ("NotBillable", "Billable"),
        ("E-A", "CHANGED"),
    ],
)
def test_bill_receipt_rejects_changed_saved_values(queued_bill, old, new):
    from kaydbooks_bridge.bill_receipt import validate_receipt

    bridge, _, _, payload = queued_bill
    policy = Config.load(bridge.config_path).companies["company-a"]
    raw = ET.tostring(saved_bill("9933"), encoding="unicode")
    assert old in raw
    with pytest.raises(BridgeError):
        validate_receipt(raw.replace(old, new), policy, payload, "9933")


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_bill_write_allowlist(queued_bill, tmp_path):
    from kaydbooks_bridge.bill_receipt import add_request

    bridge, _, _, payload = queued_bill
    policy = Config.load(bridge.config_path).companies["company-a"]
    request = add_request(policy, payload, "9933")
    file = tmp_path / "bill.xml"
    file.write_text(request)
    source = (Path(__file__).parents[1] / "src/kaydbooks_bridge/native_bill.ps1").read_text()
    methods = source[
        source.index("public static class ControlledSampleBill") : source.index(
            " public static void Run("
        )
    ]
    script = tmp_path / "bill-write-gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;\n"
        + methods
        + "}\n'@\n"
        + """
$xml=[System.IO.File]::ReadAllText($args[0])
[ControlledSampleBill]::CheckWrite($xml,[ControlledSampleBill]::Hash($xml))
foreach($bad in @($xml.Replace('BillAddRq','InvoiceAddRq'),$xml.Replace('</ExpenseLineAdd>','<BillableStatus>NotBillable</BillableStatus></ExpenseLineAdd>'),$xml.Replace('</BillAdd>','<Memo>extra</Memo></BillAdd>'),$xml.Replace('>10.00<','>-10.00<'),$xml.Replace('<ListID>V-A</ListID>','<FullName>V-A</FullName>'),$xml.Replace('</ExpenseLineAdd>','<TaxAmount>1.00</TaxAmount></ExpenseLineAdd>'),$xml.Replace('<Amount>','<Amount bad="1">'),$xml.Replace('<QBXML>','<QBXML bad="1">'))) {
 $failed=$false
 try{[ControlledSampleBill]::CheckWrite($bad,[ControlledSampleBill]::Hash($bad))}catch{$failed=$true}
 if(-not $failed){throw 'unsupported bill accepted'}
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
            str(file),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
