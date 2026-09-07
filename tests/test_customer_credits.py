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
from kaydbooks_bridge.customer_credits import (
    add_request,
    append_check,
    memo,
    plan,
    validate_check,
    validate_receipt,
)
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_credit_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case, saved_receipt  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def credit_case(receipt_case, commercial):
    policy, payload = receipt_case
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_credit_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved bounded synthetic credit testing",
            "ref_prefix": "SYN-",
            "max_credits": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    return path, token, {**payload, "invoice_txn_id": "source-id"}


def credit_row():
    row = saved_receipt()[0][0][0]
    row.tag = "CreditMemoRet"
    row.find("TxnID").text = "credit-id"
    for name in ("IsPaid", "IsFinanceCharge", "AppliedAmount"):
        row.remove(row.find(name))
    row.find("BalanceRemaining").tag = "CreditRemaining"
    row.find("InvoiceLineRet").tag = "CreditMemoLineRet"
    E.SubElement(row, "TotalAmount").text = "10.00"
    E.SubElement(row, "Memo").text = "KaydBooks invoice source-id"
    return row


class Session:
    def __init__(self):
        self.credits, self.writes = [], 0
        self.balance = Decimal("25.00")
        self.crash, self.wrong_balance = False, False
        self.mutate = None

    def xml(self, request):
        rq = E.fromstring(request)
        prefix, extras = [], []
        for q in list(rq[0]):
            if (
                q.tag == "CreditMemoQueryRq"
                or q.tag == "InvoiceQueryRq"
                or (
                    q.tag == "CustomerQueryRq"
                    and q.findtext("IncludeRetElement") == "ListID"
                    and any(n.text == "Balance" for n in q.findall("IncludeRetElement"))
                )
            ):
                extras.append(q)
                rq[0].remove(q)
            else:
                prefix.append(q)

        def single(rows):
            rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
            for key in (("Account", "ar-id"), ("Customer", "customer-id")):
                rows[key].pop("CurrencyRef")

        result = E.fromstring(
            response(E.tostring(rq, encoding="unicode"), taxable=False, mutate=single)
        )
        for q in extras:
            rs = E.SubElement(
                result[0],
                q.tag.removesuffix("Rq") + "Rs",
                requestID=q.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            if q.tag == "CustomerQueryRq":
                row = E.SubElement(rs, "CustomerRet")
                for k, v in (
                    ("ListID", "customer-id"),
                    ("IsActive", "true"),
                    ("Balance", str(self.balance)),
                ):
                    E.SubElement(row, k).text = v
            elif q.tag == "InvoiceQueryRq":
                row = saved_receipt()[0][0][0]
                row.find("TxnID").text = "source-id"
                row.find("RefNumber").text = "SYN-ORIG"
                rs.append(row)
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
            raise RuntimeError("reply lost")
        root = E.Element("QBXML")
        batch = E.SubElement(root, "QBXMLMsgsRs")
        rs = E.SubElement(
            batch,
            "CreditMemoAddRs",
            requestID=E.fromstring(write)[0][0].get("requestID"),
            statusCode="0",
            statusSeverity="Info",
        )
        rs.append(copy.deepcopy(self.credits[-1]))
        return E.tostring(root, encoding="unicode")


@pytest.fixture
def queued_credit(credit_case):
    path, token, payload = credit_case
    bridge, session = Bridge(path), Session()
    discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        credit_check=payload,
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
        operation="customer-credit.create",
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
    assert receipt["credit_remaining"] == "10.00" and receipt["balance_effects"]["after"] == "15.00"
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


@pytest.mark.parametrize(
    "case",
    [
        "prior-credit",
        "wrong-source",
        "wrong-customer",
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
        root[0][-2][0].find("TxnID").text = "wrong"
    if case == "wrong-customer":
        root[0][-2][0].find("CustomerRef/ListID").text = "wrong"
    if case == "partial-history":
        root[0][-1].set("iteratorRemainingCount", "1")
    if case == "source-item":
        root[0][-2][0].find("InvoiceLineRet/ItemRef/ListID").text = "other"
    if case == "duplicate-source-line":
        root[0][-2][0].append(copy.deepcopy(root[0][-2][0].find("InvoiceLineRet")))
    if case == "source-subtotal":
        root[0][-2][0].find("Subtotal").text = "12"
    if case == "source-tax":
        root[0][-2][0].find("SalesTaxTotal").text = "1"
    if case == "source-uom":
        E.SubElement(root[0][-2][0].find("InvoiceLineRet"), "UnitOfMeasure").text = "box"
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.parametrize(
    "field,value",
    [
        ("CreditRemaining", "9.00"),
        ("Memo", "other"),
        ("TotalAmount", "9.00"),
        ("CreditMemoLineRet/Rate", "6"),
        ("IsToBeEmailed", "true"),
    ],
)
def test_credit_saved_record_must_match(credit_case, field, value):
    path, _, payload = credit_case
    root = E.Element("QBXML")
    batch = E.SubElement(root, "QBXMLMsgsRs")
    rs = E.SubElement(
        batch, "CreditMemoQueryRs", requestID="1234", statusCode="0", statusSeverity="Info"
    )
    row = credit_row()
    row.find(field).text = value
    rs.append(row)
    with pytest.raises(BridgeError):
        validate_receipt(
            E.tostring(root), Config.load(path).companies["company-a"], payload, "1234"
        )


def test_credit_request_has_origin_and_no_application_or_delivery(credit_case):
    path, _, payload = credit_case
    root = E.fromstring(add_request(Config.load(path).companies["company-a"], payload, "1234"))
    add = root[0][0][0]
    assert add.tag == "CreditMemoAdd" and add.findtext("Memo") == memo(payload)
    assert add.findtext("IsToBePrinted") == add.findtext("IsToBeEmailed") == "false"
    assert add.find("AppliedToTxnAdd") is None and add.find("IsFinanceCharge") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_native_credit_query_gate(credit_case, tmp_path, receipt):
    path, _, payload = credit_case
    check = plan(Config.load(path).companies["company-a"], payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.customer_credits import append_lookup

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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('InvoiceQueryRq','InvoiceAddRq'),$rq.Replace('CreditRemaining','CreditCardInfo'),$rq.Replace('<TxnID>source-id</TxnID>','<RefNumber>source-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
def test_native_credit_write_gate(credit_case, tmp_path):
    path, _, payload = credit_case
    policy = Config.load(path).companies["company-a"]
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_credit.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsToBePrinted>false</IsToBePrinted>','<IsToBePrinted>true</IsToBePrinted>'),$rq.Replace('Rate','RatePercent'),$rq.Replace('CreditMemoAdd','InvoiceAdd'),$rq.Replace('<Memo>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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
