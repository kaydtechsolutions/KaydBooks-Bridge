"""Inventory bills require exact accounts, preferences and native stock increase."""

# ruff: noqa: F811
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.bill_lookup import append_check, plan, validate_check
from kaydbooks_bridge.bill_receipt import append_lookup
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.sample_bill_posting import post, reconcile, verify_stock_effect
from test_bill_lookup import exact_case, exact_response  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_sample_bills import Session, queue_case, receipt_exchange


@pytest.fixture
def inventory_case(exact_case):
    path, token, payload = exact_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["items"] = {
        "stock": {
            "list_id": "INV-A",
            "type": "inventory",
            "asset_list_id": "AS-A",
            "cogs_list_id": "CG-A",
            "income_list_id": "IN-A",
        }
    }
    path.write_text(json.dumps(raw))
    payload["lines"] = [{"item_id": "stock", "quantity": "2", "cost": "5.00", "amount": "10.00"}]
    return path, token, payload


def stock_bill(root):
    bill = root.find(".//BillRet")
    for line in bill.findall("ExpenseLineRet"):
        bill.remove(line)
    item = E.SubElement(bill, "ItemLineRet")
    E.SubElement(E.SubElement(item, "ItemRef"), "ListID").text = "INV-A"
    for key, value in [
        ("TxnLineID", "stock-line"),
        ("Quantity", "2"),
        ("Cost", "5.00"),
        ("Amount", "10.00"),
    ]:
        E.SubElement(item, key).text = value
    return root


class StockSession(Session):
    def __call__(self, request, write, folder, approve):
        def check(xml):
            root = E.fromstring(xml)
            root.find(".//ItemInventoryRet/QuantityOnHand").text = "2" if self.existing else "0"
            if root.find(".//BillRet") is not None:
                stock_bill(root)
            return approve(E.tostring(root, encoding="unicode"))

        result = super().__call__(request, write, folder, check)
        return E.tostring(stock_bill(E.fromstring(result)), encoding="unicode") if result else None


def stock_read(request, destination, quantity="2"):
    root = E.fromstring(request)
    query = root[0][-1]
    root[0].remove(query)
    receipt_exchange(E.tostring(root, encoding="unicode"), destination)
    result = stock_bill(E.fromstring(destination.read_text()))
    # Use the same synthetic inventory master response with an independently observed quantity.
    rq = E.fromstring(S._discovery_request("991", "17.0"))
    rq[0].append(query)
    rs = E.fromstring(exact_response(E.tostring(rq, encoding="unicode")))[0][-1]
    rs.find("ItemInventoryRet/QuantityOnHand").text = quantity
    rs.find("ItemInventoryRet/AverageCost").text = "5.00"
    result[0].append(rs)
    destination.write_text(E.tostring(result, encoding="unicode"))


@pytest.mark.parametrize("crash", [False, True])
def test_inventory_bill_proves_stock_effect_without_resend(inventory_case, crash):
    bridge, token, job_id, _ = queue_case(inventory_case)
    session = StockSession(crash="after" if crash else None)
    if crash:
        with pytest.raises(RuntimeError):
            post(bridge, token, "company-a", job_id, exchange=session)
        result = reconcile(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=stock_read
        )
    else:
        result = post(
            bridge, token, "company-a", job_id, exchange=session, read_exchange=stock_read
        )
    assert result["state"] == "verified" and session.writes == 1
    assert result["transaction_receipt"]["receipt"]["stock_effects"]["INV-A"]["after"] == "2"


def test_wrong_stock_after_native_write_is_held(inventory_case):
    bridge, token, job_id, _ = queue_case(inventory_case)
    session = StockSession()
    with pytest.raises(BridgeError, match="stock effect"):
        post(
            bridge,
            token,
            "company-a",
            job_id,
            exchange=session,
            read_exchange=lambda rq, d: stock_read(rq, d, "1"),
        )
    assert bridge.status(token, "company-a", job_id)["state"] == "posted-unverified"
    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=session)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


@pytest.mark.parametrize(
    "path,value",
    [
        ("AccountRet/AccountType", "Income"),
        ("ItemInventoryRet/AssetAccountRef/ListID", "other"),
        ("ItemInventoryRet/QuantityOnHand", "-1"),
        ("PreferencesRet/ItemsAndInventoryPreferences/FIFOEnabled", "true"),
        (
            "PreferencesRet/MultiLocationInventoryPreferences/IsMultiLocationInventoryEnabled",
            "true",
        ),
        ("PreferencesRet/ItemsAndInventoryPreferences/IsTrackingSerialOrLotNumber", "SerialNumber"),
        ("PreferencesRet/ItemsAndInventoryPreferences/EnhancedInventoryReceivingEnabled", "true"),
    ],
)
def test_unsupported_stock_settings_or_roles_are_rejected(inventory_case, path, value):
    config, _, payload = inventory_case
    policy = Config.load(config).companies["company-a"]
    check = plan(policy, payload)
    request = append_check(S._discovery_request("990", "17.0"), "990", check)
    root = E.fromstring(exact_response(request))
    root.find(".//" + path).text = value
    with pytest.raises(BridgeError):
        validate_check(E.tostring(root), "990", check)


def test_repeated_item_lines_aggregate_stock(inventory_case):
    config, _, payload = inventory_case
    policy = Config.load(config).companies["company-a"]
    payload["lines"] *= 2
    result = verify_stock_effect(
        policy,
        payload,
        {"INV-A": "3"},
        {"INV-A": {"quantity_on_hand": "7", "average_cost": "5.00"}},
    )
    assert result["INV-A"]["received"] == "4"
    with pytest.raises(BridgeError):
        verify_stock_effect(policy, payload, {}, {})


def test_inventory_only_company_does_not_need_an_expense_account(inventory_case):
    path, _, payload = inventory_case
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["bill_masters"]["expenses"] = {}
    path.write_text(json.dumps(raw))
    check = plan(Config.load(path).companies["company-a"], payload)
    assert all(key != "E-A" for _, key in check["queries"])


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_inventory_read_gate(inventory_case, tmp_path):
    config, _, payload = inventory_case
    policy = Config.load(config).companies["company-a"]
    requests = [
        append_check(S._discovery_request("992", "17.0"), "992", plan(policy, payload)),
        append_lookup(S._discovery_request("993", "17.0"), "993", "saved-bill", policy, payload),
    ]
    source = Path("src/kaydbooks_bridge/direct_sdk.ps1").read_text()
    methods = source[
        source.index(" static void FixedQuery(") : source.index(" public static void Run(")
    ]
    gate = source[
        source.index("   var root=doc.DocumentElement;") : source.index(
            '   Save(dir,"request.xml",request);'
        )
    ]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(requests))
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies System.Xml.dll -TypeDefinition @'\nusing System;public static class Gate {\n"
        + methods
        + "public static bool Allowed(string xml){try{var doc=new System.Xml.XmlDocument();doc.LoadXml(xml);\n"
        + gate
        + "return true;}catch{return false;}}}\n'@\n"
        + "foreach($rq in (Get-Content -Raw -LiteralPath $args[0] | ConvertFrom-Json)){if(-not [Gate]::Allowed($rq)){throw 'valid inventory rejected'};foreach($bad in @($rq.Replace('ItemInventoryQueryRq','ItemInventoryAddRq'),$rq.Replace('AverageCost','CreditCardInfo'),$rq.Replace('<ListID>INV-A</ListID>','<FullName>INV-A</FullName>'))){if([Gate]::Allowed($bad)){throw 'unsafe inventory accepted'}}}\n"
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
