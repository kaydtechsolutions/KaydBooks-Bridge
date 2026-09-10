"""Per-bill payable evidence must never be replaced by a vendor balance."""

# ruff: noqa: F811
import copy
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.bill_receipt import append_lookup, validate_lookup
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_bill_posting import post, reconcile
from test_bill_lookup import exact_case  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_sample_bills import Session, queued_bill, receipt_exchange  # noqa: F401


def balance_case(queued_bill, tmp_path):
    bridge, _, _, payload = queued_bill
    policy = Config.load(bridge.config_path).companies["company-a"]
    request = append_lookup(
        S._discovery_request("994", "17.0"), "994", "saved-bill", policy, payload
    )
    dest = tmp_path / "balance.xml"
    receipt_exchange(request, dest)
    root = ET.fromstring(dest.read_text())
    root.find(".//BillRet/OpenAmount").text = "20.00"
    return policy, payload, request, root


def test_vendor_balance_does_not_replace_bill_payable(queued_bill, tmp_path):
    policy, payload, _, root = balance_case(queued_bill, tmp_path)
    _, receipt = validate_lookup(ET.tostring(root), "994", policy, payload, "saved-bill")
    assert receipt["outstanding_amount"] == "10.00"
    assert receipt["observed_billret_open_amount"] == "20.00"
    assert receipt["balance_verification"] == "matched-bill-to-pay"


@pytest.mark.parametrize(
    "mutation",
    [
        "amount",
        "paid",
        "missing",
        "duplicate",
        "wrong-id",
        "wrong-type",
        "wrong-ap",
        "wrong-date",
        "currency",
        "uncorrelated",
        "partial",
        "extra-response",
    ],
)
def test_wrong_or_incomplete_payable_is_held(queued_bill, tmp_path, mutation):
    policy, payload, _, root = balance_case(queued_bill, tmp_path)
    rs = root[0][3]
    row = rs[0][0]
    if mutation in ("amount", "paid"):
        row.find("AmountDue").text = "20.00" if mutation == "amount" else "0.00"
    elif mutation == "missing":
        rs.remove(rs[0])
    elif mutation == "duplicate":
        rs.append(copy.deepcopy(rs[0]))
    elif mutation == "wrong-id":
        row.find("TxnID").text = "other-bill"
    elif mutation == "wrong-type":
        row.find("TxnType").text = "VendorCredit"
    elif mutation == "wrong-ap":
        row.find("APAccountRef/ListID").text = "other-ap"
    elif mutation == "wrong-date":
        row.find("DueDate").text = "2026-09-06"
    elif mutation == "currency":
        ET.SubElement(row, "CurrencyRef")
    elif mutation == "uncorrelated":
        rs.set("requestID", "9984")
    elif mutation == "partial":
        rs.set("iteratorRemainingCount", "1")
    else:
        root[0].append(copy.deepcopy(rs))
    with pytest.raises(BridgeError):
        validate_lookup(ET.tostring(root), "994", policy, payload, "saved-bill")


def test_uncertain_bill_recovers_while_paused_without_resending(queued_bill):
    bridge, token, job_id, _ = queued_bill
    session = Session(crash="after")
    with pytest.raises(RuntimeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    bridge.pause(token, "company-a", True)

    def exchange(request, dest):
        receipt_exchange(request, dest)
        dest.write_text(dest.read_text().replace("OpenAmount>10.00", "OpenAmount>20.00"))

    result = reconcile(bridge, token, "company-a", job_id, exchange=session, read_exchange=exchange)
    assert result["state"] == "verified" and session.writes == 1
    assert result["transaction_receipt"]["receipt"]["outstanding_amount"] == "10.00"
    assert bridge.status(token, "company-a")["paused"]


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_payable_allowlist(queued_bill, tmp_path):
    _, _, request, _ = balance_case(queued_bill, tmp_path)
    source = Path("src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    methods = source[
        source.index(" static void FixedQuery(") : source.index(" public static void Run(")
    ]
    gate = source[
        source.index("   var root=doc.DocumentElement;") : source.index(
            '   Save(dir,"request.xml",request);'
        )
    ]
    bad = [
        request.replace("BillToPayQueryRq", "BillPaymentCheckAddRq"),
        request.replace("</PayeeEntityRef>", "</PayeeEntityRef><DueDate>2026-09-06</DueDate>"),
        request.replace("<PayeeEntityRef>", '<PayeeEntityRef extra="1">'),
        request.replace("<ListID>V-A</ListID>", "<FullName>V-A</FullName>"),
        request.replace("<ListID>V-A</ListID>", "<ListID>V-A</ListID><ListID>other</ListID>"),
        request.replace(
            "</APAccountRef></BillToPayQueryRq>",
            "</APAccountRef><MaxReturned>1</MaxReturned></BillToPayQueryRq>",
        ),
    ]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"good": request, "bad": bad}))
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System; public static class Gate {\n"
        + methods
        + "public static bool Allowed(string xml) {try {var doc=new System.Xml.XmlDocument(); doc.LoadXml(xml);\n"
        + gate
        + "return true;}catch{return false;}}}\n'@\n"
        + "$cases=Get-Content -Raw -LiteralPath $args[0] | ConvertFrom-Json\nif(-not [Gate]::Allowed($cases.good)){throw 'valid payable rejected'}\nforeach($bad in $cases.bad){if([Gate]::Allowed($bad)){throw 'unsafe payable accepted'}}\n"
    )
    r = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
            str(cases),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert r.returncode == 0, r.stderr
