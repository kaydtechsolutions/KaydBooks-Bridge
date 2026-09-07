"""Allocation checks use synthetic data; no accounting writes are exposed."""
# ruff: noqa: F811

import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.customer_payments import append_check, plan, validate_check, validate_payload
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
def payment_case(direct):
    path, token = direct
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].append("validate")
    raw["companies"]["company-a"]["payment_masters"] = {
        "customers": {"buyer": "customer-id"},
        "receivable": "ar-id",
        "deposits": {"cash": "bank-id"},
        "methods": {"cash": "method-id"},
    }
    path.write_text(json.dumps(raw))
    payload = {
        "customer_id": "buyer",
        "deposit_id": "cash",
        "method_id": "cash",
        "txn_date": "2026-09-06",
        "ref_number": "SYN-PAY-1",
        "currency": "USD",
        "total_amount": "5.00",
        "allocations": [{"txn_id": "invoice-id", "amount": "5.00"}],
    }
    return path, token, payload


def records():
    return {
        "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
        "Company": [COMPANY_A],
        "Preferences": [{"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}}],
        "Customer": [{"ListID": "customer-id", "IsActive": "true", "Name": "Buyer"}],
        "Account": [
            {"ListID": "ar-id", "IsActive": "true", "AccountType": "AccountsReceivable"},
            {"ListID": "bank-id", "IsActive": "true", "AccountType": "Bank"},
        ],
        "PaymentMethod": [
            {"ListID": "method-id", "IsActive": "true", "Name": "Cash", "PaymentMethodType": "Cash"}
        ],
        "Invoice": [
            {
                "TxnID": "invoice-id",
                "EditSequence": "1234",
                "CustomerRef": {"ListID": "customer-id"},
                "ARAccountRef": {"ListID": "ar-id"},
                "TxnDate": "2026-09-01",
                "RefNumber": "SYN-INV",
                "IsPending": "false",
                "IsFinanceCharge": "false",
                "Subtotal": "10.00",
                "SalesTaxTotal": "0.00",
                "AppliedAmount": "0.00",
                "BalanceRemaining": "10.00",
                "IsPaid": "false",
            }
        ],
    }


def response(request, rows):
    result = E.Element("QBXML")
    batch = E.SubElement(result, "QBXMLMsgsRs")
    for query in E.fromstring(request)[0]:
        entity = query.tag.removesuffix("QueryRq")
        key = query.findtext("ListID") or query.findtext("TxnID")
        selected = [
            r for r in rows.get(entity, []) if key is None or r.get("ListID", r.get("TxnID")) == key
        ]
        root = E.Element("QBXML")
        E.SubElement(root, "QBXMLMsgsRq").append(query)
        rs = FakeQuickBooks(entities={entity: selected})(E.tostring(root, encoding="unicode"))
        batch.append(E.fromstring(rs)[0][0])
    return E.tostring(result, encoding="unicode")


def exchange(rows=None):
    def send(request, destination):
        destination.write_text(response(request, rows or records()))

    return send


def run(case, **kwargs):
    path, token, payload = case
    return discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        payment_check=payload,
        **kwargs,
    )


def test_partial_allocation_native_read_restarts_without_requery(payment_case):
    assert run(payment_case, exchange=exchange())["balances"]["invoice-id"]["balance"] == "10.00"
    assert run(payment_case, exchange=lambda *_: pytest.fail("duplicate IO"))["state"] == "verified"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["allocations"].append(dict(p["allocations"][0])),
        lambda p: p.update(total_amount="4.00"),
        lambda p: p.update(total_amount="6.00"),
        lambda p: p.update(allocations=[]),
        lambda p: p.update(customer_id="other"),
        lambda p: p.update(currency="EUR"),
        lambda p: p.update(txn_date="2026-02-30"),
        lambda p: p.update(IsAutoApply=True),
    ],
)
def test_payment_payload_rejects_ambiguity(payment_case, mutation):
    path, _, payload = payment_case
    mutation(payload)
    with pytest.raises(BridgeError):
        validate_payload(payload, Config.load(path).companies["company-a"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["Invoice"][0].update(CustomerRef={"ListID": "other"}),
        lambda r: r["Invoice"][0].update(ARAccountRef={"ListID": "other"}),
        lambda r: r["Invoice"][0].update(BalanceRemaining="4.00", AppliedAmount="-6.00"),
        lambda r: r["Invoice"][0].update(BalanceRemaining="9.00"),
        lambda r: r["Invoice"][0].update(IsPaid="true"),
        lambda r: r["Invoice"][0].update(TxnDate="2026-09-07"),
        lambda r: r["Invoice"][0].update(IsPending="true"),
        lambda r: r["Invoice"][0].update(CurrencyRef={"ListID": "currency-id"}),
        lambda r: r["Account"][1].update(AccountType="Expense"),
        lambda r: r["PaymentMethod"][0].update(PaymentMethodType="Visa"),
        lambda r: r["Customer"][0].update(IsActive="false"),
        lambda r: r.update(Company=[COMPANY_B]),
    ],
)
def test_wrong_or_overallocated_payment_evidence_blocks(payment_case, mutation):
    rows = records()
    mutation(rows)
    with pytest.raises(BridgeError):
        run(payment_case, exchange=exchange(rows))


def test_explicit_unapplied_and_undeposited_policy(payment_case):
    path, _, payload = payment_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["payment_masters"]["allow_unapplied"] = True
    path.write_text(json.dumps(raw))
    payload["allocations"] = []
    rows = records()
    rows["Account"][1].update(
        AccountType="OtherCurrentAsset", SpecialAccountType="UndepositedFunds"
    )
    assert run(payment_case, exchange=exchange(rows))["balances"] == {}


def test_paid_invoice_recovery_does_not_allow_a_new_allocation(payment_case):
    path, _, payload = payment_case
    check = plan(Config.load(path).companies["company-a"], payload)
    rows = records()
    rows["Invoice"][0].update(BalanceRemaining="0.00", AppliedAmount="-10.00", IsPaid="true")
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    xml = response(request, rows)
    with pytest.raises(BridgeError):
        validate_check(xml, "1234", check)
    assert validate_check(xml, "1234", check, recovering=True)[1]["invoice-id"]["balance"] == "0.00"


def test_revoked_validation_stops_payment_query(payment_case):
    path, _, _ = payment_case
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("validate")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        run(payment_case, exchange=lambda *_: pytest.fail("unauthorized query"))


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
@pytest.mark.parametrize("discount", [False, True])
def test_native_payment_query_gate(payment_case, tmp_path, receipt, discount):
    path, _, payload = payment_case
    from dataclasses import replace

    policy = Config.load(path).companies["company-a"]
    if discount:
        policy = replace(policy, account_roles={"customer_discount": "discount-id"})
        payload["allocations"][0].update(
            discount_amount="1.00", discount_account="customer_discount"
        )
    check = plan(policy, payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.payment_receipt import append_lookup

        request = append_lookup(
            S._discovery_request("1234", "17.0"),
            "1234",
            policy,
            payload,
            "payment-id",
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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('InvoiceQueryRq','InvoiceAddRq'),$rq.Replace('BalanceRemaining','CreditCardInfo'),$rq.Replace('<TxnID>invoice-id</TxnID>','<RefNumber>invoice-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
