"""Controlled supplier payments: durable ownership, balances and no resend."""
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
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_supplier_payment_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.supplier_payment_receipt import add_request
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401
from test_supplier_payments import payment_case, records, response  # noqa: F401


class Session:
    def __init__(self, amount="5.00", crash=False, wrong_balance=False, before=None):
        self.rows = records()
        self.rows["BillPaymentCheck"] = []
        self.amount = amount
        self.crash, self.wrong_balance, self.before = crash, wrong_balance, before
        self.writes = 0
        self.related = None
        self.unapplied = False
        self.discount = "0.00"

    def xml(self, request):
        root = E.fromstring(response(request, self.rows))
        for rs in root[0]:
            if rs.tag == "BillPaymentCheckQueryRs" and not len(rs):
                rs.set("statusCode", "500")
                rs.set("statusSeverity", "Warn")
        return E.tostring(root, encoding="unicode")

    def read(self, request, destination):
        destination.write_text(self.xml(request))

    def __call__(self, request, write, folder, approve):
        if self.before:
            self.before()
        allowed = approve(self.xml(request))
        if write is None or not allowed:
            return None
        self.writes += 1
        remaining = Decimal("10.00") - Decimal(self.amount) - Decimal(self.discount)
        self.rows["Bill"][0].update(IsPaid="true" if remaining == 0 else "false")
        self.rows["BillToPay"] = (
            []
            if remaining == 0
            else [
                {
                    "BillToPay": {
                        **self.rows["BillToPay"][0]["BillToPay"],
                        "AmountDue": str(remaining),
                    }
                }
            ]
        )
        if self.wrong_balance:
            self.rows["Bill"][0].update(IsPaid="false")
            self.rows["BillToPay"] = [
                {"BillToPay": {**records()["BillToPay"][0]["BillToPay"], "AmountDue": "9.00"}}
            ]
        payment = {
            "TxnID": "payment-id",
            "EditSequence": "2346",
            "PayeeEntityRef": {"ListID": "vendor-id"},
            "APAccountRef": {"ListID": "ap-id"},
            "TxnDate": "2026-09-06",
            "RefNumber": "SYN-PAY-1",
            "Amount": self.amount,
            "IsToBePrinted": "false",
            "BankAccountRef": {"ListID": "bank-id"},
            "AppliedToTxnRet": {
                "TxnID": "bill-id",
                "TxnType": "Bill",
                "Amount": self.amount,
                "BalanceRemaining": str(remaining),
                "TxnDate": "2026-09-01",
                "RefNumber": "SYN-INV",
            },
        }
        if Decimal(self.discount):
            payment["AppliedToTxnRet"].update(
                DiscountAmount=self.discount, DiscountAccountRef={"ListID": "discount-id"}
            )
        self.rows["BillPaymentCheck"] = [payment]
        if self.related is not None:
            payment["AppliedToTxnRet"]["LinkedTxn"] = self.related
        if self.crash:
            raise RuntimeError("response lost")
        rq = E.fromstring(write)[0][0]
        xml = self.xml(
            '<QBXML><QBXMLMsgsRq><BillPaymentCheckQueryRq requestID="'
            + rq.get("requestID")
            + '" /></QBXMLMsgsRq></QBXML>'
        )
        return xml.replace("BillPaymentCheckQueryRs", "BillPaymentCheckAddRs")


@pytest.fixture
def queued_payment(payment_case):
    path, token, payload = payment_case
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_supplier_payment_posting={
            "connector": "connector-company-a",
            "authorization": "Operator authorized controlled synthetic customer payment testing",
            "ref_prefix": "SYN-",
            "expires_at": time.time() + 3600,
            "max_payments": 3,
        },
    )
    path.write_text(json.dumps(raw))
    bridge = Bridge(path)

    def prepare(amount="5.00", unapplied=False, discount=None):
        payload["total_amount"] = payload["allocations"][0]["amount"] = amount
        if discount is not None:
            current = json.loads(path.read_text())
            current["companies"]["company-a"].setdefault("account_roles", {})[
                "supplier_discount"
            ] = "discount-id"
            path.write_text(json.dumps(current))
            payload["allocations"][0].update(
                discount_amount=discount, discount_account="supplier_discount"
            )
        session = Session(amount)
        if discount is not None:
            session.discount = discount
            session.rows["Account"].append(
                {"ListID": "discount-id", "IsActive": "true", "AccountType": "Expense"}
            )
        session.unapplied = unapplied
        run = "1234"
        discover(
            S.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            run,
            supplier_payment_check=payload,
            exchange=session.read,
        )
        namespace = Config.load(path).companies["company-a"].sources[0]
        source = documents.capture(
            bridge,
            token,
            "company-a",
            namespace,
            "payment-source",
            "application/json",
            base64.b64encode(json.dumps(payload).encode()).decode(),
        )
        job = documents.prepare(
            bridge,
            token,
            "company-a",
            source["document_id"],
            "payment-test",
            payload,
            {key: 1 for key in documents.fields(payload)},
            {"transport": "direct-sdk", "connector": "connector-company-a", "id": run},
            operation="supplier-payment.create",
        )
        bridge.action(token, "company-a", job["id"], "validate")
        preview = bridge.preview(token, "company-a", job["id"])
        assert preview["total"] == amount
        if discount is not None:
            assert preview["cash_total"] == amount
            assert preview["discount_total"] == discount
            assert preview["settlement_total"] == format(Decimal(amount) + Decimal(discount), ".2f")
        else:
            assert "discount_total" not in preview
        bridge.action(token, "company-a", job["id"], "submit")
        return bridge, token, job["id"], session

    return prepare


@pytest.mark.parametrize("amount", ["5.00", "10.00"])
@pytest.mark.parametrize("crash", [False, True])
def test_payment_settlement_and_lost_response_never_resend(queued_payment, amount, crash):
    bridge, token, job_id, session = queued_payment(amount)
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        assert bridge.status(token, "company-a", job_id)["state"] == "unknown"
        job = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    else:
        job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert job["state"] == "verified" and session.writes == 1
    assert job["transaction_receipt"]["receipt"]["balance_effects"]["bill-id"]["after"] == str(
        Decimal("10.00") - Decimal(amount)
    )
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_unexpected_balance_holds_payment_and_blocks_simulation(queued_payment):
    bridge, token, job_id, session = queued_payment()
    session.wrong_balance = True
    with pytest.raises(BridgeError, match="balance effect"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        bridge.reconcile(token, "company-a", job_id)
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1


def test_queued_payment_cannot_enter_invoice_bill_or_simulator(queued_payment):
    from kaydbooks_bridge.sample_bill_posting import post as bill_post
    from kaydbooks_bridge.sample_posting import post as invoice_post

    bridge, token, job_id, session = queued_payment()
    for post_other in (invoice_post, bill_post):
        with pytest.raises(BridgeError):
            post_other(bridge, token, "company-a", job_id, exchange=session)
    with pytest.raises(BridgeError):
        bridge.simulate(token, "company-a")
    assert bridge.status(token, "company-a", job_id)["state"] == "queued"
    assert session.writes == 0


def test_related_prior_supplier_payment_is_not_an_additional_allocation(queued_payment):
    bridge, token, job_id, session = queued_payment()
    session.related = {
        "TxnID": "prior-payment",
        "TxnType": "BillPaymentCheck",
        "LinkType": "AMTTYPE",
        "Amount": "-5.00",
    }
    job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    receipt = job["transaction_receipt"]["receipt"]
    assert job["state"] == "verified" and session.writes == 1
    assert receipt["allocations"] == {"bill-id": "5.00"}
    assert receipt["related_bill_transactions_observed"]["bill-id"][0]["txn_id"] == "prior-payment"


@pytest.mark.parametrize("change", ["grant", "mapping", "pause"])
def test_payment_authority_change_during_native_preflight(queued_payment, change):
    bridge, token, job_id, session = queued_payment()

    def mutate():
        if change == "pause":
            bridge.pause(token, "company-a", True)
        else:
            path = Path(bridge.config_path)
            raw = json.loads(path.read_text())
            if change == "grant":
                raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                    "post-sample"
                )
            else:
                raw["companies"]["company-a"]["supplier_payment_masters"]["banks"]["cash"] = (
                    "other-id"
                )
            path.write_text(json.dumps(raw))

    session.before = mutate
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0 and bridge.status(token, "company-a", job_id)["state"] == "unknown"


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("discount", [False, True])
def test_native_payment_write_and_receipt_gates(payment_case, tmp_path, discount):
    path, _, payload = payment_case
    policy = Config.load(path).companies["company-a"]
    if discount:
        from dataclasses import replace

        policy = replace(policy, account_roles={"supplier_discount": "discount-id"})
        payload["allocations"][0].update(
            discount_amount="1.00", discount_account="supplier_discount"
        )
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_supplier_payment.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsAutoApply>false</IsAutoApply>','<IsAutoApply>true</IsAutoApply>'),$rq.Replace('SYN-PAY-1','ABCDEFGHIJKL'),$rq.Replace('PaymentAmount','DiscountAmount'),$rq.Replace('BillPaymentCheckAdd','ReceivePaymentAdd'),$rq.Replace('<BankAccountRef>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>bill-id</TxnID>','<FullName>bill-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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


@pytest.mark.parametrize("amount", ["5.00", "9.00"])
@pytest.mark.parametrize("crash", [False, True])
def test_explicit_discount_settlement_and_recovery(queued_payment, amount, crash):
    bridge, token, job_id, session = queued_payment(amount, discount="1.00")
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError, match="response lost"):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        saved = reconcile(
            Bridge(bridge.config_path),
            token,
            "company-a",
            job_id,
            exchange=session,
            read_exchange=session.read,
        )
    else:
        saved = post(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    proof = saved["transaction_receipt"]["receipt"]
    assert saved["state"] == "verified" and session.writes == 1
    assert proof["total_amount"] == amount
    assert proof["settlement_discounts"]["bill-id"] == {
        "amount": "1.00",
        "account_list_id": "discount-id",
    }
    assert Decimal(proof["balance_effects"]["bill-id"]["after"]) == Decimal("9.00") - Decimal(
        amount
    )
    assert proof["balance_effects"]["bill-id"]["discount"] == "1.00"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]
