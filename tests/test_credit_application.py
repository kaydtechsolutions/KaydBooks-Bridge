"""Credit links must reconcile without inventing or replaying a payment transaction."""
# ruff: noqa: F811

import base64
import json
import os
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import documents
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.credit_application import (
    add_request,
    append_check,
    plan,
    validate_check,
    verify_effect,
)
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_application_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_customer_credits import credit_case  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def application_case(credit_case):
    path, token, _ = credit_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["sample_application_posting"] = {
        "connector": "connector-company-a",
        "authorization": "Operator authorized bounded sample credit application",
        "ref_prefix": "SYN-",
        "max_applications": 2,
        "expires_at": time.time() + 3600,
    }
    path.write_text(json.dumps(raw))
    payload = {
        "customer_id": "synthetic-customer",
        "invoice_txn_id": "invoice-id",
        "credit_txn_id": "credit-id",
        "total_amount": "3.00",
        "currency": "USD",
        "ref_number": "SYN-APPLY",
    }
    # Fixture uses its own configured alias.
    payload["customer_id"] = next(
        iter(raw["companies"]["company-a"]["invoice_masters"]["customers"])
    )
    return path, token, payload


def node(parent, name, value):
    result = E.SubElement(parent, name)
    if isinstance(value, dict):
        for k, v in value.items():
            node(result, k, v)
    else:
        result.text = str(value)


class Session:
    def __init__(self):
        self.applied, self.writes, self.crash = Decimal(0), 0, False
        self.mutate = None

    def xml(self, request):
        rq = E.fromstring(request)
        extra = list(rq[0])[2:]
        for q in extra:
            rq[0].remove(q)
        root = E.fromstring(response(E.tostring(rq, encoding="unicode")))
        rows = {
            "Preferences": {"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}},
            "Customer": {"ListID": "customer-id", "IsActive": "true", "Balance": "20.00"},
            "Account": {"ListID": "ar-id", "IsActive": "true", "AccountType": "AccountsReceivable"},
            "Invoice": {
                "TxnID": "invoice-id",
                "EditSequence": "1",
                "CustomerRef": {"ListID": "customer-id"},
                "ARAccountRef": {"ListID": "ar-id"},
                "IsPending": "false",
                "Subtotal": "10.00",
                "SalesTaxTotal": "0.00",
                "BalanceRemaining": str(Decimal(10) - self.applied),
                "IsPaid": "false",
            },
            "CreditMemo": {
                "TxnID": "credit-id",
                "EditSequence": "2",
                "CustomerRef": {"ListID": "customer-id"},
                "ARAccountRef": {"ListID": "ar-id"},
                "IsPending": "false",
                "TotalAmount": "5.00",
                "SalesTaxTotal": "0.00",
                "CreditRemaining": str(Decimal(5) - self.applied),
            },
        }
        if self.applied:
            rows["Invoice"]["LinkedTxn"] = {
                "TxnID": "credit-id",
                "TxnType": "CreditMemo",
                "LinkType": "AMTTYPE",
                "Amount": str(-self.applied),
            }
            rows["CreditMemo"]["LinkedTxn"] = {
                "TxnID": "invoice-id",
                "TxnType": "Invoice",
                "LinkType": "AMTTYPE",
                "Amount": str(-self.applied),
            }
        for q in extra:
            entity = q.tag.removesuffix("QueryRq")
            rs = E.SubElement(
                root[0],
                entity + "QueryRs",
                requestID=q.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            node(rs, entity + "Ret", rows[entity])
        if self.mutate:
            self.mutate(root)
        return E.tostring(root, encoding="unicode")

    def read(self, request, destination):
        destination.write_text(self.xml(request))

    def __call__(self, request, write, folder, approve):
        allowed = approve(self.xml(request))
        if write is None or not allowed:
            return None
        self.writes += 1
        self.applied = Decimal(E.fromstring(write).findtext(".//AppliedAmount"))
        if self.crash:
            raise RuntimeError("lost application reply")
        run = E.fromstring(write)[0][0].get("requestID")
        return f'<QBXML><QBXMLMsgsRs><ReceivePaymentAddRs requestID="{run}" statusCode="0" statusSeverity="Info"><ReceivePaymentRet><AppliedToTxnRet><TxnID>invoice-id</TxnID><TxnType>Invoice</TxnType></AppliedToTxnRet></ReceivePaymentRet></ReceivePaymentAddRs></QBXMLMsgsRs></QBXML>'


@pytest.fixture
def queued_application(application_case):
    path, token, payload = application_case
    bridge, session = Bridge(path), Session()
    discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        application_check=payload,
        exchange=session.read,
    )
    source = documents.capture(
        bridge,
        token,
        "company-a",
        Config.load(path).companies["company-a"].sources[0],
        "apply-source",
        "application/json",
        base64.b64encode(json.dumps(payload).encode()).decode(),
    )
    job = documents.prepare(
        bridge,
        token,
        "company-a",
        source["document_id"],
        "application-test",
        payload,
        {k: 1 for k in documents.fields(payload)},
        {"transport": "direct-sdk", "connector": "connector-company-a", "id": "1234"},
        operation="customer-credit.apply",
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "3.00"
    bridge.action(token, "company-a", job["id"], "submit")
    return bridge, token, job["id"], session


@pytest.mark.parametrize("crash", [False, True])
def test_application_links_and_balances_recover_without_a_payment(queued_application, crash):
    bridge, token, job_id, session = queued_application
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
    assert receipt["new_transaction_created"] is False
    assert receipt["balance_effects"]["after"]["credit"] == "2.00"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


@pytest.mark.parametrize(
    "case",
    [
        "customer",
        "ar",
        "credit",
        "tax",
        "already-linked",
        "insufficient",
        "currency",
        "duplicate-link",
    ],
)
def test_application_rejects_bad_preflight(application_case, case):
    path, _, payload = application_case
    check = plan(Config.load(path).companies["company-a"], payload)
    session = Session()
    if case in ("already-linked", "duplicate-link"):
        session.applied = Decimal(1)
    root = E.fromstring(
        session.xml(append_check(S._discovery_request("1234", "17.0"), "1234", check))
    )
    row = root[0][-1][0]
    if case == "customer":
        row.find("CustomerRef/ListID").text = "wrong"
    if case == "ar":
        row.find("ARAccountRef/ListID").text = "wrong"
    if case == "credit":
        row.find("TxnID").text = "wrong"
    if case == "tax":
        row.find("SalesTaxTotal").text = "1"
    if case == "insufficient":
        row.find("CreditRemaining").text = "2"
    if case == "currency":
        node(row, "CurrencyRef", {"ListID": "other"})
    if case == "duplicate-link":
        row.append(E.fromstring(E.tostring(row.find("LinkedTxn"))))
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.parametrize("field", ["invoice", "credit", "customer", "invoice_link", "credit_link"])
def test_each_independent_balance_or_link_is_required(application_case, field):
    payload = application_case[2]
    before = {
        "invoice": "10",
        "credit": "5",
        "customer": "20",
        "invoice_link": "0",
        "credit_link": "0",
    }
    after = {
        "invoice": "7",
        "credit": "2",
        "customer": "20",
        "invoice_link": "-3",
        "credit_link": "-3",
    }
    assert verify_effect(payload, before, after)["new_transaction_created"] is False
    after[field] = "99"
    with pytest.raises(BridgeError):
        verify_effect(payload, before, after)


def test_request_only_links_existing_transactions(application_case):
    path, _, payload = application_case
    add = E.fromstring(add_request(Config.load(path).companies["company-a"], payload, "1234"))[0][
        0
    ][0]
    assert [x.tag for x in add] == ["CustomerRef", "ARAccountRef", "AppliedToTxnAdd"]
    assert add.findtext("AppliedToTxnAdd/SetCredit/AppliedAmount") == "3.00"


def test_application_revocation_before_dispatch_prevents_write(queued_application):
    bridge, token, job_id, session = queued_application
    raw = json.loads(bridge.config_path.read_text())
    for principal in raw["principals"].values():
        grants = principal["companies"].get("company-a", [])
        if "post-sample" in grants:
            grants.remove("post-sample")
    bridge.config_path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert session.writes == 0


def test_application_readback_mismatch_holds_and_cannot_enter_simulator(queued_application):
    bridge, token, job_id, session = queued_application

    def change(root):
        if session.applied:
            root.find(".//CustomerRet/Balance").text = "19.00"

    session.mutate = change
    with pytest.raises(BridgeError, match="balances or reciprocal links"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        bridge.simulate(token, "company-a")
    with pytest.raises(BridgeError):
        bridge.reconcile(token, "company-a", job_id)
    assert session.writes == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_native_application_query_gate(application_case, tmp_path, receipt):
    path, _, payload = application_case
    check = plan(Config.load(path).companies["company-a"], payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.credit_application import append_lookup

        request = append_lookup(
            S._discovery_request("1234", "17.0"),
            "1234",
            Config.load(path).companies["company-a"],
            payload,
            "invoice-id",
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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('InvoiceQueryRq','InvoiceAddRq'),$rq.Replace('CreditRemaining','CreditCardInfo'),$rq.Replace('<TxnID>invoice-id</TxnID>','<RefNumber>invoice-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
def test_native_application_write_gate(application_case, tmp_path):
    path, _, payload = application_case
    policy = Config.load(path).companies["company-a"]
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_application.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsToBePrinted>false</IsToBePrinted>','<IsToBePrinted>true</IsToBePrinted>'),$rq.Replace('AppliedAmount','PaymentAmount'),$rq.Replace('ReceivePaymentAdd','InvoiceAdd'),$rq.Replace('<ARAccountRef>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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


def test_manual_approval_and_dispatch_are_distinct(application_case, monkeypatch):
    path, token, payload = application_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["approval_required"] = True
    path.write_text(json.dumps(raw))
    approver = "synthetic-approver-" + "a" * 32
    monkeypatch.setenv("KAYDBOOKS_APPROVER_A_SECRET", approver)
    bridge, session = Bridge(path), Session()
    discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        application_check=payload,
        exchange=session.read,
    )
    source = documents.capture(
        bridge,
        token,
        "company-a",
        Config.load(path).companies["company-a"].sources[0],
        "apply-source",
        "application/json",
        base64.b64encode(json.dumps(payload).encode()).decode(),
    )
    job = documents.prepare(
        bridge,
        token,
        "company-a",
        source["document_id"],
        "application-test",
        payload,
        {k: 1 for k in documents.fields(payload)},
        {"transport": "direct-sdk", "connector": "connector-company-a", "id": "1234"},
        operation="customer-credit.apply",
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "3.00"
    with pytest.raises(BridgeError, match="approval required"):
        bridge.action(token, "company-a", job["id"], "submit")
    bridge.action(approver, "company-a", job["id"], "approve")
    assert session.writes == 0
    bridge.action(token, "company-a", job["id"], "submit")
    assert session.writes == 0
    result = post(
        bridge, token, "company-a", job["id"], exchange=session, read_exchange=session.read
    )
    assert result["state"] == "verified" and session.writes == 1
    events = [
        e["event"] for e in bridge.audit(token, "company-a")["events"] if e["job_id"] == job["id"]
    ]
    assert (
        events.index("approve")
        < events.index("submit")
        < events.index("application_write_authorized")
        < events.index("native_application_verified")
    )
