"""Controlled native dispatch tests use a synthetic session, never QuickBooks."""
# ruff: noqa: F811

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.invoice_receipt import add_request
from kaydbooks_bridge.sample_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.store import Store
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case, saved_receipt  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_receipt_lifecycle import receipt_exchange, saved_job  # noqa: F401


@pytest.fixture
def queued(saved_job):
    bridge, token, job_id, envelope, _ = saved_job
    path = Path(bridge.config_path)
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].append("post-sample")
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved controlled synthetic invoice tests",
            "ref_prefix": "SYN-",
            "max_invoices": 3,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    bridge.action(token, "company-a", job_id, "submit")
    return bridge, token, job_id, envelope


class Session:
    def __init__(self, *, existing=False, crash=None, before=None):
        self.existing, self.crash, self.before = existing, crash, before
        self.writes = 0

    def __call__(self, request, write, folder, approve):
        rq = ET.fromstring(request)
        collision = rq[0][-1]
        assert collision.tag == "InvoiceQueryRq" and collision.findtext("RefNumber") == "SYN-CHECK"
        rq[0].remove(collision)

        def single(rows):
            rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
            for kind, key in (("Account", "ar-id"), ("Customer", "customer-id")):
                rows[(kind, key)].pop("CurrencyRef")

        root = ET.fromstring(
            response(ET.tostring(rq, encoding="unicode"), taxable=False, mutate=single)
        )
        if self.existing:
            rs = saved_receipt()[0][0]
            rs.set("requestID", collision.get("requestID"))
        else:
            rs = ET.Element(
                "InvoiceQueryRs",
                requestID=collision.get("requestID"),
                statusCode="500",
                statusSeverity="Warn",
            )
        root[0].append(rs)
        if self.before:
            self.before()
        allowed = approve(ET.tostring(root, encoding="unicode"))
        if write is None or not allowed:
            return None
        if self.crash == "before-write":
            raise RuntimeError("parent died before send")
        self.writes += 1
        self.existing = True
        if self.crash == "after-write":
            raise RuntimeError("response lost")
        result = saved_receipt("InvoiceAdd")
        result[0][0].set("requestID", ET.fromstring(write)[0][0].get("requestID"))
        return ET.tostring(result, encoding="unicode")


def test_native_lifecycle_and_duplicate_request(queued):
    bridge, token, job_id, envelope = queued
    session = Session()
    result = post(
        bridge, token, "company-a", job_id, exchange=session, read_exchange=receipt_exchange()
    )
    assert result["state"] == "verified" and result["txn_id"] == "saved-id"
    assert result["attempt"] and result["transaction_receipt"]
    assert session.writes == 1
    with pytest.raises(BridgeError, match="never retry"):
        post(bridge, token, "company-a", job_id, exchange=session)
    envelope.pop("master_evidence")
    assert bridge.prepare(token, "company-a", envelope)["id"] == job_id
    assert bridge.audit(token, "company-a")["valid"]


def test_matching_existing_invoice_is_not_written(queued):
    bridge, token, job_id, _ = queued
    session = Session(existing=True)
    assert (
        post(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=receipt_exchange()
        )["state"]
        == "verified"
    )
    assert session.writes == 0


def test_uncertain_write_reconciles_without_resending(queued):
    bridge, token, job_id, _ = queued
    session = Session(crash="after-write")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert Bridge(bridge.config_path).status(token, "company-a", job_id)["state"] == "unknown"
    with pytest.raises(BridgeError, match="never retry"):
        post(bridge, token, "company-a", job_id, exchange=session)
    result = reconcile(
        Bridge(bridge.config_path),
        token,
        "company-a",
        job_id,
        exchange=session,
        read_exchange=receipt_exchange(),
    )
    assert result["state"] == "verified" and session.writes == 1
    with pytest.raises(BridgeError):
        bridge.reconcile(token, "company-a", job_id)


def test_absence_after_interruption_does_not_authorize_retry(queued):
    bridge, token, job_id, _ = queued
    session = Session(crash="before-write")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    with pytest.raises(BridgeError, match="inconclusive"):
        reconcile(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0 and bridge.status(token, "company-a", job_id)["state"] == "unknown"


@pytest.mark.parametrize("change", ["pause", "permission", "policy", "scope", "expired"])
def test_changed_authority_stops_before_write(queued, change):
    bridge, token, job_id, _ = queued

    def mutate():
        path = Path(bridge.config_path)
        raw = json.loads(path.read_text())
        if change == "pause":
            bridge.pause(token, "company-a", True)
        elif change == "permission":
            raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                "post-sample"
            )
        elif change == "policy":
            raw["companies"]["company-a"]["account_roles"]["invoice_receivable"] = "other"
        elif change == "scope":
            raw["companies"]["company-a"]["sample_posting"]["ref_prefix"] = "OTHER-"
        else:
            raw["companies"]["company-a"]["sample_posting"]["expires_at"] = 1
        path.write_text(json.dumps(raw))

    session = Session(before=mutate)
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0


def test_missing_gate_and_quota_reject_before_dispatch(queued):
    bridge, token, job_id, _ = queued
    path = Path(bridge.config_path)
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"].pop("sample_posting")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="authorization"):
        post(
            bridge,
            token,
            "company-a",
            job_id,
            exchange=lambda *_: pytest.fail("dispatch forbidden"),
        )
    store = Store(Config.load(path).root, "company-a")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM native_invoice_attempts").fetchone()[0] == 0


def test_changed_binding_cannot_reconcile_an_old_attempt(queued):
    bridge, token, job_id, _ = queued
    session = Session(crash="after-write")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    path = Path(bridge.config_path)
    raw = json.loads(path.read_text())
    raw["connectors"]["connector-company-a"]["identity_sha256"] = "a" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="original dispatch context"):
        reconcile(bridge, token, "company-a", job_id, exchange=session)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_snapshot_gate_rejects_changes(receipt_case, tmp_path):
    policy, payload = receipt_case
    request = add_request(policy, payload, "1234")
    file = tmp_path / "request.xml"
    file.write_text(request)
    source = (Path(__file__).parents[1] / "src/kaydbooks_bridge/native_invoice.ps1").read_text()
    methods = source[
        source.index("public static class ControlledSampleInvoice") : source.index(
            " public static void Run("
        )
    ]
    script = tmp_path / "native-write-gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;\n"
        + methods
        + "}\n'@\n"
        + """
$xml=[System.IO.File]::ReadAllText($args[0])
[ControlledSampleInvoice]::CheckWrite($xml,$args[1])
$failed=$false
try {[ControlledSampleInvoice]::CheckWrite($xml.Replace('>false<','>true<'),$args[1])}catch{$failed=$true}
if(-not $failed){throw 'changed snapshot accepted'}
foreach($bad in @($xml.Replace('InvoiceAddRq','BillAddRq'),$xml.Replace('>false<','>true<'),$xml.Replace('</InvoiceAdd>','<Memo>extra</Memo></InvoiceAdd>'),$xml.Replace('</InvoiceLineAdd>','<OverrideItemAccountRef><ListID>other</ListID></OverrideItemAccountRef></InvoiceLineAdd>'))) {
 $failed=$false
 try{[ControlledSampleInvoice]::CheckWrite($bad,[ControlledSampleInvoice]::Hash($bad))}catch{$failed=$true}
 if(-not $failed){throw 'unsupported request accepted'}
}
Write-Output 'Native write gate passed'
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
            hashlib.sha256(request.encode()).hexdigest(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
