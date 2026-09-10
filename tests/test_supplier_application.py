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
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_supplier_application_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.supplier_application import (
    add_request,
    append_check,
    plan,
    validate_check,
    verify_effect,
)
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
    raw["companies"]["company-a"]["sample_supplier_application_posting"] = {
        "connector": "connector-company-a",
        "authorization": "Operator authorized bounded sample credit application",
        "ref_prefix": "SYN-",
        "max_applications": 2,
        "expires_at": time.time() + 3600,
    }
    raw["companies"]["company-a"]["supplier_payment_masters"] = {
        "vendors": {"vendor": "vendor-id"},
        "payable": "ap-id",
        "banks": {"cash": "bank-id"},
    }
    path.write_text(json.dumps(raw))
    payload = {
        "vendor_id": "vendor",
        "bank_id": "cash",
        "bill_txn_id": "bill-id",
        "credit_txn_id": "credit-id",
        "total_amount": "3.00",
        "currency": "USD",
        "ref_number": "SYN-APPLY",
    }
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
            "Vendor": {"ListID": "vendor-id", "IsActive": "true", "Balance": "20.00"},
            "Bill": {
                "TxnID": "bill-id",
                "EditSequence": "1",
                "VendorRef": {"ListID": "vendor-id"},
                "APAccountRef": {"ListID": "ap-id"},
                "TxnDate": "2026-09-01",
                "AmountDue": "10.00",
                "IsPaid": "true" if self.applied == 10 else "false",
            },
            "VendorCredit": {
                "TxnID": "credit-id",
                "EditSequence": "2",
                "VendorRef": {"ListID": "vendor-id"},
                "APAccountRef": {"ListID": "ap-id"},
                "TxnDate": "2026-09-01",
                "CreditAmount": "5.00",
            },
        }
        if self.applied:
            for kind, other, txn in (
                ("Bill", "VendorCredit", "credit-id"),
                ("VendorCredit", "Bill", "bill-id"),
            ):
                rows[kind]["LinkedTxn"] = {
                    "TxnID": txn,
                    "TxnType": other,
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
            if entity == "Account":
                key = q.findtext("ListID")
                row = {
                    "ListID": key,
                    "IsActive": "true",
                    "AccountType": "Bank" if key == "bank-id" else "AccountsPayable",
                    "Balance": "100.00",
                }
            elif entity == "BillToPay":
                for kind, txn, total, field, typ in (
                    ("BillToPay", "bill-id", 10, "AmountDue", "Bill"),
                    ("CreditToApply", "credit-id", 5, "CreditRemaining", "VendorCredit"),
                ):
                    if total - self.applied > 0:
                        node(
                            rs,
                            "BillToPayRet",
                            {
                                kind: {
                                    "TxnID": txn,
                                    "TxnType": typ,
                                    "APAccountRef": {"ListID": "ap-id"},
                                    field: str(Decimal(total) - self.applied),
                                }
                            },
                        )
                continue
            else:
                row = rows[entity]
            node(rs, entity + "Ret", row)
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
        return f'<QBXML><QBXMLMsgsRs><BillPaymentCheckAddRs requestID="{run}" statusCode="0" statusSeverity="Info"><BillPaymentCheckRet><AppliedToTxnRet><TxnID>bill-id</TxnID><TxnType>Bill</TxnType></AppliedToTxnRet></BillPaymentCheckRet></BillPaymentCheckAddRs></QBXMLMsgsRs></QBXML>'


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
        supplier_application_check=payload,
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
        operation="supplier-credit.apply",
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
    assert receipt["cash_movement"] is False
    assert receipt["balance_effects"]["after"]["credit"] == "2.00"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


@pytest.mark.parametrize(
    "case",
    [
        "vendor",
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
    row = root.find(".//VendorCreditRet")
    if case == "vendor":
        row.find("VendorRef/ListID").text = "wrong"
    if case == "ar":
        row.find("APAccountRef/ListID").text = "wrong"
    if case == "credit":
        row.find("TxnID").text = "wrong"
    if case == "tax":
        node(row, "IsTaxIncluded", "true")
    if case == "insufficient":
        root.find(".//CreditToApply/CreditRemaining").text = "2"
    if case == "currency":
        node(row, "CurrencyRef", {"ListID": "other"})
    if case == "duplicate-link":
        row.append(E.fromstring(E.tostring(row.find("LinkedTxn"))))
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.parametrize("field", ["bill", "credit", "vendor", "bank", "bill_link", "credit_link"])
def test_each_independent_balance_or_link_is_required(application_case, field):
    payload = application_case[2]
    before = {
        "bill": "10",
        "credit": "5",
        "vendor": "20",
        "bank": "100",
        "bill_link": "0",
        "credit_link": "0",
    }
    after = {
        "bill": "7",
        "credit": "2",
        "vendor": "20",
        "bank": "100",
        "bill_link": "-3",
        "credit_link": "-3",
    }
    assert verify_effect(payload, before, after)["cash_movement"] is False
    after[field] = "99"
    with pytest.raises(BridgeError):
        verify_effect(payload, before, after)


def test_request_only_links_existing_transactions(application_case):
    path, _, payload = application_case
    add = E.fromstring(add_request(Config.load(path).companies["company-a"], payload, "1234"))[0][
        0
    ][0]
    assert [x.tag for x in add] == [
        "PayeeEntityRef",
        "APAccountRef",
        "BankAccountRef",
        "RefNumber",
        "AppliedToTxnAdd",
    ]
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
            root.find(".//VendorRet/Balance").text = "19.00"

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
        from kaydbooks_bridge.supplier_application import append_lookup

        request = append_lookup(
            S._discovery_request("1234", "17.0"),
            "1234",
            Config.load(path).companies["company-a"],
            payload,
            "stub-id",
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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('BillQueryRq','BillAddRq'),$rq.Replace('CreditAmount','CreditCardInfo'),$rq.Replace('<TxnID>bill-id</TxnID>','<RefNumber>bill-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
    payload["ref_number"] = "ABCDEFGHIJK"
    policy = Config.load(path).companies["company-a"]
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_supplier_application.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsToBePrinted>false</IsToBePrinted>','<IsToBePrinted>true</IsToBePrinted>'),$rq.Replace('ABCDEFGHIJK','ABCDEFGHIJKL'),$rq.Replace('AppliedAmount','PaymentAmount'),$rq.Replace('BillPaymentCheckAdd','BillAdd'),$rq.Replace('<APAccountRef>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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
        supplier_application_check=payload,
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
        operation="supplier-credit.apply",
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
        < events.index("supplier_application_write_authorized")
        < events.index("native_supplier_application_verified")
    )


@pytest.mark.parametrize(
    "case", ["incomplete", "duplicate", "negative", "missing", "wrong-ap", "wrong-bank"]
)
def test_complete_exact_payables_required(application_case, case):
    path, _, payload = application_case
    check = plan(Config.load(path).companies["company-a"], payload)
    root = E.fromstring(
        Session().xml(append_check(S._discovery_request("1234", "17.0"), "1234", check))
    )
    rs = root[0][-1]
    if case == "incomplete":
        rs.set("iteratorRemainingCount", "1")
    if case == "duplicate":
        rs.append(E.fromstring(E.tostring(rs[0])))
    if case == "negative":
        rs.find(".//AmountDue").text = "-1"
    if case == "missing":
        rs.remove(rs[-1])
    if case == "wrong-ap":
        rs.find(".//APAccountRef/ListID").text = "other"
    if case == "wrong-bank":
        root[0][5][0].find("ListID").text = "other"
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "1234", check)


def test_exhausted_credit_absent_from_complete_payables(application_case):
    path, _, payload = application_case
    payload = {**payload, "total_amount": "5.00"}
    check = plan(Config.load(path).companies["company-a"], payload)
    session = Session()
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    _, before = validate_check(session.xml(request), "1234", check)
    session.applied = Decimal(5)
    _, after = validate_check(session.xml(request), "1234", check, recovering=True)
    assert verify_effect(payload, before, after)["after"]["credit"] == "0"


def test_application_cannot_acknowledge_a_new_payment(application_case):
    from kaydbooks_bridge.supplier_application import validate_receipt

    path, _, payload = application_case
    xml = '<QBXML><QBXMLMsgsRs><BillPaymentCheckAddRs requestID="1234" statusCode="0" statusSeverity="Info"><BillPaymentCheckRet><TxnID>new-id</TxnID><EditSequence>1</EditSequence><AppliedToTxnRet><TxnID>bill-id</TxnID><TxnType>Bill</TxnType></AppliedToTxnRet></BillPaymentCheckRet></BillPaymentCheckAddRs></QBXMLMsgsRs></QBXML>'
    with pytest.raises(BridgeError, match="unexpected new payment"):
        validate_receipt(xml, Config.load(path).companies["company-a"], payload, "1234")


def test_registered_supplier_tools_forward_exact_arguments(application_case, monkeypatch):
    import asyncio

    from kaydbooks_bridge.hermes_tools import Tools, server

    path, token, payload = application_case
    calls = []
    monkeypatch.setattr(Tools, "call", lambda self, *args: calls.append(args) or {"ok": True})
    app = server(path, token)

    async def exercise():
        for name in ("prepare_supplier_application_v1", "prepare_supplier_credit_v1"):
            args = {
                "company": "company-a",
                "document_id": "doc",
                "idempotency_key": "key",
                "payload": payload,
                "confidence": {},
                "master_evidence": {},
            }
            await app._tool_manager.call_tool(name, args)
            assert calls[-1][0:2] == (name, "company-a")
            assert calls[-1][2] == {k: v for k, v in args.items() if k != "company"}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "query-omits-allocation",
        "amount",
        "vendor",
        "bank",
        "bill",
        "printed",
        "memo",
        "currency",
        "payment",
    ],
)
def test_generated_zero_stub_is_independently_verified(application_case, case):
    from kaydbooks_bridge.supplier_application import (
        append_lookup,
        validate_lookup,
        validate_receipt,
    )

    path, _, payload = application_case
    policy = Config.load(path).companies["company-a"]
    root = E.Element("QBXML")
    batch = E.SubElement(root, "QBXMLMsgsRs")
    rs = E.SubElement(
        batch, "BillPaymentCheckQueryRs", requestID="123410", statusCode="0", statusSeverity="Info"
    )
    node(
        rs,
        "BillPaymentCheckRet",
        {
            "TxnID": "stub-id",
            "EditSequence": "1",
            "PayeeEntityRef": {"ListID": "vendor-id"},
            "APAccountRef": {"ListID": "ap-id"},
            "BankAccountRef": {"ListID": "bank-id"},
            "IsToBePrinted": "false",
            "Memo": "QuickBooks generated zero amount transaction for bill payment stub",
            "AppliedToTxnRet": {"TxnID": "bill-id", "TxnType": "Bill"},
        },
    )
    row = rs[0]
    if case == "amount":
        node(row, "Amount", "2.00")
    if case == "vendor":
        row.find("PayeeEntityRef/ListID").text = "other"
    if case == "bank":
        row.find("BankAccountRef/ListID").text = "other"
    if case == "bill":
        row.find("AppliedToTxnRet/TxnID").text = "other"
    if case == "printed":
        row.find("IsToBePrinted").text = "true"
    if case == "memo":
        row.find("Memo").text = "Unrelated payment"
    if case == "currency":
        node(row, "CurrencyRef", {"ListID": "other"})
    if case == "payment":
        node(row.find("AppliedToTxnRet"), "PaymentAmount", "2.00")
    if case == "query-omits-allocation":
        row.remove(row.find("AppliedToTxnRet"))
    if case not in ("valid", "query-omits-allocation"):
        with pytest.raises(BridgeError):
            validate_receipt(
                E.tostring(root),
                policy,
                payload,
                "123410",
                operation="BillPaymentCheckQuery",
                txn_id="stub-id",
            )
        return
    session = Session()
    session.applied = Decimal(3)
    request = append_lookup(
        S._discovery_request("1234", "17.0"), "1234", policy, payload, "stub-id"
    )
    request_root = E.fromstring(request)
    request_root[0].remove(request_root[0][-1])
    result = E.fromstring(session.xml(E.tostring(request_root)))
    result[0].append(rs)
    _, receipt = validate_lookup(E.tostring(result), "1234", policy, payload, "stub-id")
    assert receipt["new_transaction_created"] and receipt["payment_stub"]["zero_amount_stub"]
    assert receipt["cash_movement"] is False and receipt["balances"]["bank"] == "100.00"


def test_supplier_application_rejects_overlong_reference_before_write(application_case):
    path, _, payload = application_case
    policy = Config.load(path).companies["company-a"]
    payload["ref_number"] = "ABCDEFGHIJKL"
    with pytest.raises(BridgeError, match="1-11"):
        plan(policy, payload)
