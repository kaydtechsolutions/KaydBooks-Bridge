"""Recorded refunds verify unused credit, customer and bank without processor calls."""
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
from kaydbooks_bridge.config import PERMISSIONS, BridgeError, Config
from kaydbooks_bridge.customer_refunds import (
    add_request,
    append_check,
    plan,
    validate_check,
    validate_payload,
)
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_refund_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_customer_payments import payment_case, records, response  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def refund_case(payment_case):
    path, token, payload = payment_case
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_refund_posting={
            "connector": "connector-company-a",
            "authorization": "Operator authorized bounded recorded sample refund",
            "ref_prefix": "SYN-",
            "max_refunds": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    payload["allocations"][0]["txn_id"] = "credit-id"
    return path, token, payload


class Session:
    def __init__(self):
        self.rows = records()
        self.rows["Customer"][0]["Balance"] = "20.00"
        for row in self.rows["Account"]:
            row["Balance"] = "100.00"
        self.rows["PaymentMethod"][0]["PaymentMethodType"] = "Visa"
        self.rows["CreditMemo"] = [
            {
                "TxnID": "credit-id",
                "EditSequence": "123",
                "CustomerRef": {"ListID": "customer-id"},
                "ARAccountRef": {"ListID": "ar-id"},
                "TxnDate": "2026-09-01",
                "IsPending": "false",
                "TotalAmount": "10.00",
                "SalesTaxTotal": "0.00",
                "CreditRemaining": "10.00",
            }
        ]
        self.rows["ARRefundCreditCard"] = []
        self.writes, self.crash, self.wrong_bank = 0, False, False

    def xml(self, request):
        root = E.fromstring(response(request, self.rows))
        for rs in root[0]:
            if rs.tag == "ARRefundCreditCardQueryRs" and not len(rs):
                rs.set("statusCode", "500")
                rs.set("statusSeverity", "Warn")
        # Native refund queries omit this field even when the Add response includes it.
        for allocation in root.findall(
            ".//ARRefundCreditCardQueryRs/ARRefundCreditCardRet/RefundAppliedToTxnRet"
        ):
            remaining = allocation.find("CreditRemaining")
            if remaining is not None:
                allocation.remove(remaining)
        return E.tostring(root, encoding="unicode")

    def read(self, request, destination):
        destination.write_text(self.xml(request))

    def __call__(self, request, write, folder, approve):
        allowed = approve(self.xml(request))
        if write is None or not allowed:
            return None
        self.writes += 1
        self.rows["Customer"][0]["Balance"] = "25.00"
        self.rows["Account"][1]["Balance"] = "99.00" if self.wrong_bank else "95.00"
        self.rows["CreditMemo"][0]["CreditRemaining"] = "5.00"
        row = {
            "TxnID": "refund-id",
            "EditSequence": "124",
            "CustomerRef": {"ListID": "customer-id"},
            "ARAccountRef": {"ListID": "ar-id"},
            "RefundFromAccountRef": {"ListID": "bank-id"},
            "PaymentMethodRef": {"ListID": "method-id"},
            "TxnDate": "2026-09-06",
            "RefNumber": "SYN-PAY-1",
            "TotalAmount": "5.00",
            "RefundAppliedToTxnRet": {
                "TxnID": "credit-id",
                "TxnType": "CreditMemo",
                "TxnDate": "2026-09-01",
                "CreditRemaining": "5.00",
                "RefundAmount": "5.00",
            },
        }
        self.rows["ARRefundCreditCard"] = [row]
        if self.crash:
            raise RuntimeError("lost refund reply")
        query = E.Element("QBXML")
        batch = E.SubElement(query, "QBXMLMsgsRq")
        E.SubElement(
            batch, "ARRefundCreditCardQueryRq", requestID=E.fromstring(write)[0][0].get("requestID")
        )
        return response(E.tostring(query, encoding="unicode"), self.rows).replace(
            "ARRefundCreditCardQueryRs", "ARRefundCreditCardAddRs"
        )


@pytest.fixture
def queued_refund(refund_case):
    path, token, payload = refund_case
    bridge, session = Bridge(path), Session()
    discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        refund_check=payload,
        exchange=session.read,
    )
    source = documents.capture(
        bridge,
        token,
        "company-a",
        Config.load(path).companies["company-a"].sources[0],
        "refund-source",
        "application/json",
        base64.b64encode(json.dumps(payload).encode()).decode(),
    )
    job = documents.prepare(
        bridge,
        token,
        "company-a",
        source["document_id"],
        "refund-test",
        payload,
        {k: 1 for k in documents.fields(payload)},
        {"transport": "direct-sdk", "connector": "connector-company-a", "id": "1234"},
        operation="customer-refund.create",
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "5.00"
    bridge.action(token, "company-a", job["id"], "submit")
    return bridge, token, job["id"], session


@pytest.mark.parametrize("crash", [False, True])
def test_refund_and_lost_reply_verify_without_resend(queued_refund, crash):
    bridge, token, job_id, session = queued_refund
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
    assert job["transaction_receipt"]["receipt"]["payment_processor_invoked"] is False
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_refund_bank_mismatch_holds(queued_refund):
    bridge, token, job_id, session = queued_refund
    session.wrong_bank = True
    with pytest.raises(BridgeError, match="effect differs"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        bridge.simulate(token, "company-a")
    assert session.writes == 1


@pytest.mark.parametrize(
    "case", ["method", "bank", "customer", "credit", "unapplied", "processing"]
)
def test_refund_rejects_incompatible_or_processing_input(refund_case, case):
    path, _, payload = refund_case
    session = Session()
    if case == "method":
        session.rows["PaymentMethod"][0]["PaymentMethodType"] = "Cash"
    if case == "bank":
        session.rows["Account"][1]["AccountType"] = "Expense"
    if case == "customer":
        session.rows["CreditMemo"][0]["CustomerRef"]["ListID"] = "other"
    if case == "credit":
        session.rows["CreditMemo"][0]["CreditRemaining"] = "1.00"
    if case == "unapplied":
        payload["allocations"] = []
    if case == "processing":
        payload["CreditCardTxnInfo"] = {"CreditCardNumber": "synthetic"}
    with pytest.raises(BridgeError):
        policy = Config.load(path).companies["company-a"]
        check = plan(policy, validate_payload(payload, policy))
        validate_check(
            session.xml(append_check(S._discovery_request("1234", "17.0"), "1234", check)),
            "1234",
            check,
        )


def test_refund_request_has_no_processor_data(refund_case):
    path, _, payload = refund_case
    add = E.fromstring(add_request(Config.load(path).companies["company-a"], payload, "1234"))[0][
        0
    ][0]
    assert add.tag == "ARRefundCreditCardAdd" and add.find("CreditCardTxnInfo") is None
    assert Decimal(add.findtext("RefundAppliedToTxnAdd/RefundAmount")) == 5


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
def test_native_refund_query_gate(refund_case, tmp_path, receipt):
    path, _, payload = refund_case
    check = plan(Config.load(path).companies["company-a"], payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.customer_refunds import append_lookup

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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('CreditMemoQueryRq','CreditMemoAddRq'),$rq.Replace('CreditRemaining','CreditCardInfo'),$rq.Replace('<TxnID>credit-id</TxnID>','<RefNumber>credit-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
def test_native_refund_write_gate(refund_case, tmp_path):
    path, _, payload = refund_case
    policy = Config.load(path).companies["company-a"]
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_refund.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsToBePrinted>false</IsToBePrinted>','<IsToBePrinted>true</IsToBePrinted>'),$rq.Replace('RefundAmount','PaymentAmount'),$rq.Replace('ARRefundCreditCardAdd','InvoiceAdd'),$rq.Replace('<ARAccountRef>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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


@pytest.mark.parametrize("change", ["revoked", "expired", "company"])
def test_refund_dispatch_rechecks_authority(queued_refund, change):
    bridge, token, job_id, session = queued_refund

    def exchange(request, write, folder, approve):
        raw = json.loads(bridge.config_path.read_text())
        if change == "revoked":
            raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                "post-sample"
            )
        elif change == "expired":
            raw["companies"]["company-a"]["sample_refund_posting"]["expires_at"] = 1
        else:
            raw["connectors"]["connector-company-a"]["identity_sha256"] = "e" * 64
        bridge.config_path.write_text(json.dumps(raw))
        return session(request, write, folder, approve)

    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=exchange, read_exchange=session.read)
    assert session.writes == 0


@pytest.mark.parametrize("change", ["customer", "credit", "duplicate", "processor"])
def test_refund_readback_conflict_never_replays(queued_refund, change):
    bridge, token, job_id, session = queued_refund

    def exchange(request, write, folder, approve):
        result = session(request, write, folder, approve)
        if change == "customer":
            session.rows["Customer"][0]["Balance"] = "26.00"
        elif change == "credit":
            session.rows["CreditMemo"][0]["CreditRemaining"] = "6.00"
        elif change == "duplicate":
            session.rows["ARRefundCreditCard"].append(dict(session.rows["ARRefundCreditCard"][0]))
        else:
            result = result.replace(
                "</ARRefundCreditCardRet>",
                "<CreditCardTxnInfo><CreditCardTxnResultInfo /></CreditCardTxnInfo></ARRefundCreditCardRet>",
            )
        return result

    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=exchange, read_exchange=session.read)
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


@pytest.mark.parametrize(
    "method", ["AmericanExpress", "Discover", "MasterCard", "OtherCreditCard", "Visa"]
)
def test_supported_credit_card_method_enum(refund_case, method):
    path, _, payload = refund_case
    session = Session()
    session.rows["PaymentMethod"][0]["PaymentMethodType"] = method
    check = plan(Config.load(path).companies["company-a"], payload)
    validate_check(
        session.xml(append_check(S._discovery_request("1234", "17.0"), "1234", check)),
        "1234",
        check,
    )
