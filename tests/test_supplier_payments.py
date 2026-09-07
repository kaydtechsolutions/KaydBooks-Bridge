"""Supplier payment checks use synthetic companies and independently scoped payables."""
# ruff: noqa: F811

import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.supplier_payments import append_check, plan, validate_check, validate_payload
from test_customer_payments import response
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import COMPANY_A, HOST, PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def payment_case(direct):
    path, token = direct
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].append("validate")
    raw["companies"]["company-a"]["supplier_payment_masters"] = {
        "vendors": {"vendor": "vendor-id"},
        "payable": "ap-id",
        "banks": {"cash": "bank-id"},
    }
    path.write_text(json.dumps(raw))
    payload = {
        "vendor_id": "vendor",
        "bank_id": "cash",
        "txn_date": "2026-09-06",
        "ref_number": "SYN-PAY-1",
        "currency": "USD",
        "total_amount": "5.00",
        "allocations": [{"txn_id": "bill-id", "amount": "5.00"}],
    }
    return path, token, payload


def records():
    return {
        "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
        "Company": [COMPANY_A],
        "Preferences": [{"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}}],
        "Vendor": [
            {"ListID": "vendor-id", "Name": "Vendor", "IsActive": "true", "Balance": "40.00"}
        ],
        "Account": [
            {"ListID": "ap-id", "IsActive": "true", "AccountType": "AccountsPayable"},
            {"ListID": "bank-id", "IsActive": "true", "AccountType": "Bank"},
        ],
        "Bill": [
            {
                "TxnID": "bill-id",
                "EditSequence": "1234",
                "VendorRef": {"ListID": "vendor-id"},
                "APAccountRef": {"ListID": "ap-id"},
                "TxnDate": "2026-09-01",
                "DueDate": "2026-10-01",
                "RefNumber": "SYN-BILL-1",
                "AmountDue": "10.00",
                "OpenAmount": "40.00",
                "IsPaid": "false",
            }
        ],
        "BillToPay": [
            {
                "BillToPay": {
                    "TxnID": "bill-id",
                    "TxnType": "Bill",
                    "APAccountRef": {"ListID": "ap-id"},
                    "TxnDate": "2026-09-01",
                    "DueDate": "2026-10-01",
                    "RefNumber": "SYN-BILL-1",
                    "AmountDue": "10.00",
                }
            }
        ],
    }


def test_supplier_preflight_uses_bill_to_pay_not_vendor_open_amount(payment_case):
    path, token, payload = payment_case

    def exchange(request, destination):
        destination.write_text(response(request, records()))

    result = discover(
        S.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        supplier_payment_check=payload,
        exchange=exchange,
    )
    assert result["state"] == "verified"
    assert result["balances"]["bill-id"]["balance"] == "10.00"
    assert result["balances"]["bill-id"]["open_amount_observed"] == "40.00"


@pytest.mark.parametrize(
    "change",
    ["vendor", "bank", "amount", "missing", "duplicate", "incomplete", "paid", "date", "currency"],
)
def test_supplier_preflight_rejects_invalid_payable_evidence(payment_case, change):
    path, _, payload = payment_case
    policy = Config.load(path).companies["company-a"]
    rows = records()
    if change == "vendor":
        rows["Bill"][0]["VendorRef"]["ListID"] = "other"
    if change == "bank":
        rows["Account"][1]["AccountType"] = "Expense"
    if change == "amount":
        rows["BillToPay"][0]["BillToPay"]["AmountDue"] = "4.00"
    if change == "missing":
        rows["BillToPay"] = []
    if change == "duplicate":
        rows["BillToPay"] *= 2
    if change == "paid":
        rows["Bill"][0]["IsPaid"] = "true"
    if change == "date":
        rows["BillToPay"][0]["BillToPay"]["TxnDate"] = "2025-01-01"
    if change == "currency":
        rows["Preferences"][0]["MultiCurrencyPreferences"]["IsMultiCurrencyOn"] = "true"
    check = plan(policy, payload)
    xml = response(append_check(S._discovery_request("1234", "17.0"), "1234", check), rows)
    if change == "incomplete":
        root = E.fromstring(xml)
        root[0][-1].set("iteratorRemainingCount", "1")
        xml = E.tostring(root)
    with pytest.raises(BridgeError):
        validate_check(xml, "1234", check)


@pytest.mark.parametrize("change", ["duplicate", "zero", "empty", "remainder", "credit", "print"])
def test_supplier_payload_rejects_unsupported_applications(payment_case, change):
    path, _, payload = payment_case
    if change == "duplicate":
        payload["allocations"] *= 2
    if change == "zero":
        payload["allocations"][0]["amount"] = "0.00"
    if change == "empty":
        payload["allocations"] = []
    if change == "remainder":
        payload["total_amount"] = "6.00"
    if change == "credit":
        payload["allocations"][0]["credit_id"] = "credit-id"
    if change == "print":
        payload["is_to_be_printed"] = True
    with pytest.raises(BridgeError):
        validate_payload(payload, Config.load(path).companies["company-a"])


def test_duplicate_payable_amount_cannot_hide_a_different_balance(payment_case):
    path, _, payload = payment_case
    check = plan(Config.load(path).companies["company-a"], payload)
    xml = response(append_check(S._discovery_request("1234", "17.0"), "1234", check), records())
    root = E.fromstring(xml)
    E.SubElement(root[0][-1][0][0], "AmountDue").text = "1.00"
    with pytest.raises(BridgeError, match="ambiguous"):
        validate_check(E.tostring(root), "1234", check)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
@pytest.mark.parametrize("receipt", [False, True])
@pytest.mark.parametrize("discount", [False, True])
def test_native_supplier_payment_query_gate(payment_case, tmp_path, receipt, discount):
    path, _, payload = payment_case
    from dataclasses import replace

    policy = Config.load(path).companies["company-a"]
    if discount:
        policy = replace(policy, account_roles={"supplier_discount": "discount-id"})
        payload["allocations"][0].update(
            discount_amount="1.00", discount_account="supplier_discount"
        )
    check = plan(policy, payload)
    request = append_check(S._discovery_request("1234", "17.0"), "1234", check)
    if receipt:
        from kaydbooks_bridge.supplier_payment_receipt import append_lookup

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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid payment rejected'}\nforeach($bad in @($rq.Replace('BillQueryRq','BillAddRq'),$rq.Replace('OpenAmount','CreditCardInfo'),$rq.Replace('<TxnID>bill-id</TxnID>','<RefNumber>bill-id</RefNumber>'))){if([Gate]::Allowed($bad)){throw 'unsafe payment query accepted'}}\n"
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
