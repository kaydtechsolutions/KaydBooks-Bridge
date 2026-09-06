"""Controlled customer payments: durable ownership, balances and no resend."""
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
from kaydbooks_bridge.payment_receipt import add_request
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_payment_posting import post, reconcile
from kaydbooks_bridge.service import Bridge
from test_customer_payments import payment_case, records, response  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401


class Session:
    def __init__(self, amount="5.00", crash=False, wrong_balance=False, before=None):
        self.rows = records()
        self.rows["ReceivePayment"] = []
        self.amount = amount
        self.crash, self.wrong_balance, self.before = crash, wrong_balance, before
        self.writes = 0
        self.related = None
        self.unapplied = False
        self.other_credits = "0.00"

    def xml(self, request):
        root = E.fromstring(response(request, self.rows))
        for rs in root[0]:
            if rs.tag == "ReceivePaymentQueryRs" and not len(rs):
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
        remaining = Decimal("10.00") - Decimal(self.amount)
        self.rows["Invoice"][0].update(
            EditSequence="2345",
            AppliedAmount=str(-Decimal(self.amount)),
            BalanceRemaining=str(remaining),
            IsPaid="true" if remaining == 0 else "false",
        )
        if self.wrong_balance:
            self.rows["Invoice"][0].update(
                AppliedAmount="-1.00", BalanceRemaining="9.00", IsPaid="false"
            )
        payment = {
            "TxnID": "payment-id",
            "EditSequence": "2346",
            "CustomerRef": {"ListID": "customer-id"},
            "ARAccountRef": {"ListID": "ar-id"},
            "TxnDate": "2026-09-06",
            "RefNumber": "SYN-PAY-1",
            "TotalAmount": self.amount,
            "PaymentMethodRef": {"ListID": "method-id"},
            "DepositToAccountRef": {"ListID": "bank-id"},
            "UnusedPayment": "0.00",
            "UnusedCredits": self.other_credits,
            "AppliedToTxnRet": {
                "TxnID": "invoice-id",
                "TxnType": "Invoice",
                "Amount": self.amount,
                "BalanceRemaining": str(remaining),
                "TxnDate": "2026-09-01",
                "RefNumber": "SYN-INV",
            },
        }
        self.rows["ReceivePayment"] = [payment]
        if self.unapplied:
            self.rows["Invoice"] = records()["Invoice"]
            payment["UnusedPayment"] = self.amount
            payment.pop("AppliedToTxnRet")
        if self.related is not None:
            payment["AppliedToTxnRet"]["LinkedTxn"] = self.related
        if self.crash:
            raise RuntimeError("response lost")
        rq = E.fromstring(write)[0][0]
        xml = self.xml(
            '<QBXML><QBXMLMsgsRq><ReceivePaymentQueryRq requestID="'
            + rq.get("requestID")
            + '" /></QBXMLMsgsRq></QBXML>'
        )
        return xml.replace("ReceivePaymentQueryRs", "ReceivePaymentAddRs")


@pytest.fixture
def queued_payment(payment_case):
    path, token, payload = payment_case
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_payment_posting={
            "connector": "connector-company-a",
            "authorization": "Operator authorized controlled synthetic customer payment testing",
            "ref_prefix": "SYN-",
            "expires_at": time.time() + 3600,
            "max_payments": 3,
        },
    )
    path.write_text(json.dumps(raw))
    bridge = Bridge(path)

    def prepare(amount="5.00", unapplied=False):
        if unapplied:
            raw = json.loads(path.read_text())
            raw["companies"]["company-a"]["payment_masters"]["allow_unapplied"] = True
            path.write_text(json.dumps(raw))
        payload["total_amount"] = payload["allocations"][0]["amount"] = amount
        if unapplied:
            payload["allocations"] = []
        session = Session(amount)
        session.unapplied = unapplied
        run = "1234"
        discover(
            S.from_path(path),
            token,
            "connector-company-a",
            PASSWORD_A,
            run,
            payment_check=payload,
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
            operation="customer-payment.create",
        )
        bridge.action(token, "company-a", job["id"], "validate")
        assert bridge.preview(token, "company-a", job["id"])["total"] == amount
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
    assert job["transaction_receipt"]["receipt"]["balance_effects"]["invoice-id"]["after"] == str(
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


def test_related_prior_payment_is_not_an_additional_allocation(queued_payment):
    bridge, token, job_id, session = queued_payment()
    session.related = {
        "TxnID": "prior-payment",
        "TxnType": "ReceivePayment",
        "LinkType": "AMTTYPE",
        "Amount": "-5.00",
    }
    job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    receipt = job["transaction_receipt"]["receipt"]
    assert job["state"] == "verified" and session.writes == 1
    assert receipt["allocations"] == {"invoice-id": "5.00"}
    assert (
        receipt["related_invoice_transactions_observed"]["invoice-id"][0]["txn_id"]
        == "prior-payment"
    )


@pytest.mark.parametrize("crash", [False, True])
def test_other_customer_credits_do_not_change_current_payment_allocation(queued_payment, crash):
    bridge, token, job_id, session = queued_payment()
    session.other_credits = "5.00"
    session.crash = crash
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
        job = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read
        )
    else:
        job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    receipt = job["transaction_receipt"]["receipt"]
    assert job["state"] == "verified" and session.writes == 1
    assert receipt["other_customer_credits_observed"] == "5.00"
    assert receipt["unused_payment"] == "0.00"
    assert receipt["allocations"] == {"invoice-id": "5.00"}
    assert receipt["balance_effects"]["invoice-id"]["after"] == "5.00"


def test_invalid_customer_credit_observation_holds_saved_payment(queued_payment):
    bridge, token, job_id, session = queued_payment()
    session.other_credits = "-5.00"
    with pytest.raises(BridgeError, match="unused balance"):
        post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "unknown"
    assert session.writes == 1


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
                raw["companies"]["company-a"]["payment_masters"]["deposits"]["cash"] = "other-id"
            path.write_text(json.dumps(raw))

    session.before = mutate
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 0 and bridge.status(token, "company-a", job_id)["state"] == "unknown"


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("unapplied", [False, True])
def test_native_payment_write_and_receipt_gates(payment_case, tmp_path, unapplied):
    path, _, payload = payment_case
    policy = Config.load(path).companies["company-a"]
    if unapplied:
        from dataclasses import replace

        policy = replace(
            policy, payment_masters={**policy.payment_masters, "allow_unapplied": True}
        )
        payload["allocations"] = []
    request = add_request(policy, payload, "1234998")
    source = Path("src/kaydbooks_bridge/native_payment.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    file = tmp_path / "write.xml"
    file.write_text(request)
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;using System.IO;using System.Xml;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\n$rq=Get-Content -Raw -LiteralPath $args[0]\n[Gate]::CheckWrite($rq,[Gate]::Hash($rq))\nforeach($bad in @($rq.Replace('<IsAutoApply>false</IsAutoApply>','<IsAutoApply>true</IsAutoApply>'),$rq.Replace('PaymentAmount','DiscountAmount'),$rq.Replace('ReceivePaymentAdd','BillPaymentCheckAdd'),$rq.Replace('<PaymentMethodRef>','<CreditCardTxnInfo>'),$rq.Replace('<TxnID>invoice-id</TxnID>','<FullName>invoice-id</FullName>'))){if($bad -eq $rq){continue};$rejected=$false;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$rejected=$true};if(-not $rejected){throw 'unsafe payment write accepted'}}\n"
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


def test_unapplied_customer_deposit_stays_unallocated(queued_payment):
    bridge, token, job_id, session = queued_payment(unapplied=True)
    job = post(bridge, token, "company-a", job_id, exchange=session, read_exchange=session.read)
    receipt = job["transaction_receipt"]["receipt"]
    assert job["state"] == "verified" and session.writes == 1
    assert receipt["unused_payment"] == "5.00" and receipt["allocations"] == {}
    assert receipt["balance_effects"] == {} and receipt["invoice_balances"] == {}
