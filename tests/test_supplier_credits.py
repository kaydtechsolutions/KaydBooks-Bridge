"""Credit qualification: original invoice, prior credits, saved lines and AR effect."""
# ruff: noqa: F811

import base64
import copy
import json
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import documents
from kaydbooks_bridge.config import PERMISSIONS, BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_supplier_credit_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.supplier_credits import (
    add_request,
    append_check,
    plan,
    validate_check,
)
from test_bill_lookup import exact_case, exact_response  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401
from test_sample_bills import saved_bill


@pytest.fixture
def credit_case(exact_case):
    path, token, payload = exact_case
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_supplier_credit_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved bounded synthetic supplier credit test",
            "ref_prefix": "BILL-",
            "max_credits": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    payload.pop("due_date")
    payload["bill_txn_id"] = "source-id"
    return path, token, payload


def credit_row():
    row = saved_bill("1234")[0][0][0]
    row.tag = "VendorCreditRet"
    row.find("TxnID").text = "credit-id"
    row.find("AmountDue").tag = "CreditAmount"
    for field in ("DueDate", "IsPaid"):
        row.remove(row.find(field))
    E.SubElement(row, "Memo").text = "KaydBooks bill source-id"
    return row


class Session:
    def __init__(self):
        self.credits, self.writes = [], 0
        self.balance = Decimal("25.00")
        self.crash, self.wrong_balance = False, False
        self.mutate = None

    def xml(self, request):
        rq = E.fromstring(request)
        extras = []
        for q in list(rq[0]):
            if q.tag in ("VendorCreditQueryRq", "BillQueryRq", "BillToPayQueryRq") or (
                q.tag == "VendorQueryRq"
                and any(n.text == "Balance" for n in q.findall("IncludeRetElement"))
            ):
                extras.append(q)
                rq[0].remove(q)
        result = E.fromstring(exact_response(E.tostring(rq, encoding="unicode")))
        for q in extras:
            rs = E.SubElement(
                result[0],
                q.tag.removesuffix("Rq") + "Rs",
                requestID=q.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            if q.tag == "VendorQueryRq":
                row = E.SubElement(rs, "VendorRet")
                for k, v in (
                    ("ListID", "V-A"),
                    ("IsActive", "true"),
                    ("Balance", str(self.balance)),
                ):
                    E.SubElement(row, k).text = v
            elif q.tag == "BillQueryRq":
                row = saved_bill("1234")[0][0][0]
                row.find("TxnID").text = "source-id"
                rs.append(row)
            elif q.tag == "BillToPayQueryRq":
                row = E.SubElement(E.SubElement(rs, "BillToPayRet"), "BillToPay")
                for k, v in (("TxnID", "source-id"), ("TxnType", "Bill"), ("AmountDue", "25.00")):
                    E.SubElement(row, k).text = v
                E.SubElement(E.SubElement(row, "APAccountRef"), "ListID").text = "AP-A"
                for credit in self.credits:
                    row = E.SubElement(E.SubElement(rs, "BillToPayRet"), "CreditToApply")
                    for k, v in (
                        ("TxnID", credit.findtext("TxnID")),
                        ("TxnType", "VendorCredit"),
                        ("CreditRemaining", "10.00"),
                    ):
                        E.SubElement(row, k).text = v
                    E.SubElement(E.SubElement(row, "APAccountRef"), "ListID").text = "AP-A"
            else:
                for row in self.credits:
                    if (
                        q.find("TxnID") is None or q.findtext("TxnID") == row.findtext("TxnID")
                    ) and (
                        q.find("RefNumber") is None
                        or q.findtext("RefNumber") == row.findtext("RefNumber")
                    ):
                        rs.append(copy.deepcopy(row))
                if not len(rs):
                    rs.set("statusCode", "500")
                    rs.set("statusSeverity", "Warn")
        if self.mutate:
            self.mutate(result)
        return E.tostring(result, encoding="unicode")

    def read(self, request, destination):
        destination.write_text(self.xml(request))

    def __call__(self, request, write, folder, approve):
        allowed = approve(self.xml(request))
        if write is None or not allowed:
            return None
        self.writes += 1
        self.credits.append(credit_row())
        self.balance -= Decimal("1.00" if self.wrong_balance else "10.00")
        if self.crash:
            raise RuntimeError("lost supplier credit reply")
        root = E.Element("QBXML")
        batch = E.SubElement(root, "QBXMLMsgsRs")
        rs = E.SubElement(
            batch,
            "VendorCreditAddRs",
            requestID=E.fromstring(write)[0][0].get("requestID"),
            statusCode="0",
            statusSeverity="Info",
        )
        rs.append(copy.deepcopy(self.credits[-1]))
        return E.tostring(root, encoding="unicode")


@pytest.fixture
def queued_credit(credit_case):
    return queue_credit(credit_case)


def queue_credit(credit_case, session=None):
    path, token, payload = credit_case
    bridge, session = Bridge(path), session or Session()
    discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        supplier_credit_check=payload,
        exchange=session.read,
    )
    namespace = Config.load(path).companies["company-a"].sources[0]
    source = documents.capture(
        bridge,
        token,
        "company-a",
        namespace,
        "credit-source",
        "application/json",
        base64.b64encode(json.dumps(payload).encode()).decode(),
    )
    job = documents.prepare(
        bridge,
        token,
        "company-a",
        source["document_id"],
        "credit-test",
        payload,
        {key: 1 for key in documents.fields(payload)},
        {"transport": "direct-sdk", "connector": "connector-company-a", "id": "1234"},
        operation="supplier-credit.create",
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "10.00"
    bridge.action(token, "company-a", job["id"], "submit")
    return bridge, token, job["id"], session


@pytest.mark.parametrize("crash", [False, True])
def test_credit_note_verifies_customer_effect_and_never_resends(queued_credit, crash):
    bridge, token, job_id, session = queued_credit
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        job = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    else:
        job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert job["state"] == "verified" and session.writes == 1
    receipt = job["transaction_receipt"]["receipt"]
    assert receipt["unused_credit"] == "10.00" and receipt["balance_effects"]["after"] == "15.00"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_credit_balance_mismatch_holds_result(queued_credit):
    bridge, token, job_id, session = queued_credit
    session.wrong_balance = True
    with pytest.raises(BridgeError, match="balance effect"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        bridge.simulate(token, "company-a")
    assert session.writes == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_native_supplier_credit_query_gate(credit_case, tmp_path, receipt):
    path, _, payload = credit_case
    check = plan(Config.load(path).companies["company-a"], payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.supplier_credits import append_lookup

        request = append_lookup(
            S._discovery_request("1234", "17.0"),
            "1234",
            Config.load(path).companies["company-a"],
            payload,
            "credit-id",
        )
    source = Path("src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    methods = source[
        source.index(" static void FixedQuery(") : source.index(" public static void Run(")
    ]
    gate = source[
        source.index("   var root=doc.DocumentElement;") : source.index(
            '   Save(dir,"request.xml",request);'
        )
    ]
    file = tmp_path / "request.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;public static class Gate {\n"
        + methods
        + "public static bool Allowed(string xml){try{var doc=new System.Xml.XmlDocument();doc.LoadXml(xml);\n"
        + gate
        + "return true;}catch{return false;}}}\n'@\n"
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('BillQueryRq','BillAddRq'),$rq.Replace('CreditAmount','CreditCardInfo'),$rq.Replace('<TxnID>source-id</TxnID>','<RefNumber>source-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_supplier_credit_write_gate(credit_case, tmp_path):
    path, _, payload = credit_case
    policy = Config.load(path).companies["company-a"]
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_supplier_credit.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsToBePrinted>false</IsToBePrinted>','<IsToBePrinted>true</IsToBePrinted>'),$rq.Replace('Rate','RatePercent'),$rq.Replace('VendorCreditAdd','InvoiceAdd'),$rq.Replace('<Memo>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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


@pytest.mark.parametrize(
    "case",
    [
        "prior-credit",
        "wrong-source",
        "wrong-vendor",
        "partial-history",
        "duplicate-credit",
        "source-item",
        "duplicate-source-line",
        "source-subtotal",
        "source-tax",
        "source-uom",
    ],
)
def test_credit_source_and_prior_history_are_required(credit_case, case):
    path, _, payload = credit_case
    session = Session()
    if case in ("prior-credit", "duplicate-credit"):
        prior = credit_row()
        prior.find("RefNumber").text = "SYN-PRIOR"
        session.credits = [prior] * (2 if case == "duplicate-credit" else 1)
    check = plan(Config.load(path).companies["company-a"], payload)
    root = E.fromstring(
        session.xml(append_check(S._discovery_request("1234", "17.0"), "1234", check))
    )
    if case == "wrong-source":
        root[0][-3][0].find("TxnID").text = "wrong"
    if case == "wrong-vendor":
        root[0][-3][0].find("VendorRef/ListID").text = "wrong"
    if case == "partial-history":
        root[0][-2].set("iteratorRemainingCount", "1")
    if case == "source-item":
        root[0][-3][0].find("ExpenseLineRet/AccountRef/ListID").text = "other"
    if case == "duplicate-source-line":
        root[0][-3][0].append(copy.deepcopy(root[0][-3][0].find("ExpenseLineRet")))
    if case == "source-subtotal":
        root[0][-3][0].find("AmountDue").text = "12"
    if case == "source-tax":
        E.SubElement(root[0][-3][0], "IsTaxIncluded").text = "true"
    if case == "source-uom":
        E.SubElement(root[0][-3][0].find("ExpenseLineRet"), "UnitOfMeasure").text = "box"
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.parametrize("change", ["revoked", "expired", "company"])
def test_supplier_credit_dispatch_rechecks_authority(queued_credit, change):
    bridge, token, job_id, session = queued_credit

    def exchange(request, write, folder, approve):
        raw = json.loads(bridge.config_path.read_text())
        if change == "revoked":
            raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                "post-sample"
            )
        elif change == "expired":
            raw["companies"]["company-a"]["sample_supplier_credit_posting"]["expires_at"] = 1
        else:
            raw["connectors"]["connector-company-a"]["identity_sha256"] = "e" * 64
        bridge.config_path.write_text(json.dumps(raw))
        return session(request, write, folder, approve)

    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=exchange, read_exchange=session.read)
    assert session.writes == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("Memo", "other"),
        ("CreditAmount", "9.00"),
        ("ExpenseLineRet/Amount", "9.00"),
        ("APAccountRef/ListID", "other"),
    ],
)
def test_saved_supplier_credit_matches(credit_case, field, value):
    from kaydbooks_bridge.supplier_credits import validate_receipt

    path, _, payload = credit_case
    root = E.Element("QBXML")
    batch = E.SubElement(root, "QBXMLMsgsRs")
    rs = E.SubElement(
        batch, "VendorCreditQueryRs", requestID="1234", statusCode="0", statusSeverity="Info"
    )
    row = credit_row()
    row.find(field).text = value
    rs.append(row)
    with pytest.raises(BridgeError):
        validate_receipt(
            E.tostring(root), Config.load(path).companies["company-a"], payload, "1234"
        )


@pytest.mark.parametrize("kind", ["missing", "amount", "type", "ap"])
def test_unused_supplier_credit_is_independent(queued_credit, kind):
    bridge, token, job_id, session = queued_credit

    def mutate(root):
        row = root.find(".//CreditToApply")
        if row is None:
            return
        if kind == "missing":
            row.remove(row.find("CreditRemaining"))
        if kind == "amount":
            row.find("CreditRemaining").text = "9.00"
        if kind == "type":
            row.find("TxnType").text = "Bill"
        if kind == "ap":
            row.find("APAccountRef/ListID").text = "other"

    session.mutate = mutate
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


def test_mixed_supplier_credit_saved_lines_and_balances(credit_case, monkeypatch):
    path, token, payload = credit_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["items"] = {
        "service": {"type": "service", "list_id": "I-A", "expense_id": "office"}
    }
    path.write_text(json.dumps(raw))
    payload["lines"] = [
        {"expense_id": "office", "amount": "5.00"},
        {"item_id": "service", "quantity": "2", "cost": "2.50", "amount": "5.00"},
    ]

    def mixed(row):
        row.find("ExpenseLineRet/Amount").text = "5.00"
        line = E.SubElement(row, "ItemLineRet")
        for k, v in (
            ("TxnLineID", "item-line"),
            ("Quantity", "2"),
            ("Cost", "2.50"),
            ("Amount", "5.00"),
        ):
            E.SubElement(line, k).text = v
        E.SubElement(E.SubElement(line, "ItemRef"), "ListID").text = "I-A"
        return row

    original = credit_row
    monkeypatch.setattr(__import__(__name__), "credit_row", lambda: mixed(original()))
    session = Session()

    def source(root):
        for row in root.findall(".//BillRet"):
            mixed(row)

    session.mutate = source
    bridge, token, job_id, session = queue_credit(credit_case, session)
    job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert job["state"] == "verified" and session.writes == 1
    assert len(job["transaction_receipt"]["receipt"]["line_ids"]) == 2
