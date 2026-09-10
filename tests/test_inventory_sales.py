"""Inventory sales require native stock decrease and safe sold-out recovery."""

# ruff: noqa: F811
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.invoice_receipt import append_lookup, lookup_context, validate_lookup
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.qbwc_invoices import append_request, make_plan
from kaydbooks_bridge.sample_posting import post, reconcile, verify_stock_effect
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_receipt_lifecycle import receipt_exchange, saved_job  # noqa: F401
from test_sample_posting import Session, queued  # noqa: F401


@pytest.fixture
def receipt_case(commercial):
    path, _, payload = commercial
    raw = json.loads(path.read_text())
    masters = raw["companies"]["company-a"]["invoice_masters"]
    masters["currency_id"] = None
    masters["commercial"].update(tax_item_id=None, tax_rate="0")
    next(iter(masters["items"].values())).update(
        kind="Inventory", cogs_account_id="cogs-id", asset_account_id="asset-id"
    )
    path.write_text(json.dumps(raw))
    payload["tax_amount"] = "0.00"
    return Config.load(path).companies["company-a"], payload


class StockSession(Session):
    def __call__(self, request, write, folder, approve):
        def check(xml):
            root = E.fromstring(xml)
            item = root.find(".//ItemInventoryRet")
            item.find("QuantityOnHand").text = "0" if self.existing else "2"
            item.find("QuantityOnSalesOrder").text = "0"
            return approve(E.tostring(root, encoding="unicode"))

        return super().__call__(request, write, folder, check)


def stock_read(request, destination, quantity="0"):
    root = E.fromstring(request)
    query = root[0][-1]
    root[0].remove(query)
    receipt_exchange()(E.tostring(root, encoding="unicode"), destination)
    rq = E.fromstring(S._discovery_request("990", "17.0"))
    rq[0].append(query)
    result = E.fromstring(destination.read_text())
    rs = E.fromstring(response(E.tostring(rq, encoding="unicode")))[0][-1]
    rs.find("ItemInventoryRet/QuantityOnHand").text = quantity
    result[0].append(rs)
    destination.write_text(E.tostring(result, encoding="unicode"))


@pytest.mark.parametrize("crash", [False, True])
def test_sold_out_inventory_invoice_recovers_without_resending(queued, crash):
    bridge, token, job_id, _ = queued
    session = StockSession(crash="after-write" if crash else None)
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
    assert result["transaction_receipt"]["receipt"]["stock_effects"]["service-id"]["after"] == "0"


def test_wrong_stock_reduction_is_held(queued):
    bridge, token, job_id, _ = queued
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
    assert session.writes == 1


def test_stock_receipt_context_and_qbwc_request_require_inventory(queued, tmp_path):
    bridge, _, _, envelope = queued
    policy = Config.load(bridge.config_path).companies["company-a"]
    payload = envelope["payload"]
    request = append_lookup(S._discovery_request("995", "17.0"), "995", "saved-id", policy, payload)
    assert (
        append_request(
            S._discovery_request("995", "17.0"), "995", make_plan(policy, payload, "saved-id")
        )
        == request
    )
    dest = tmp_path / "response.xml"
    stock_read(request, dest)
    root = E.fromstring(dest.read_text())
    root[0].remove(root[0][-1])
    with pytest.raises(BridgeError):
        validate_lookup(E.tostring(root), "995", policy, payload, "saved-id")
    assert lookup_context(policy, payload, "saved-id")


def test_repeated_inventory_sales_sum_and_missing_baseline_rejected(receipt_case):
    policy, payload = receipt_case
    payload["lines"] *= 2
    r = verify_stock_effect(
        policy, payload, {"service-id": "4"}, {"service-id": {"quantity_on_hand": "0"}}
    )
    assert r["service-id"]["sold"] == "4"
    with pytest.raises(BridgeError):
        verify_stock_effect(policy, payload, None, {"service-id": {"quantity_on_hand": "0"}})


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_native_inventory_sales_receipt_gate(receipt_case, tmp_path):
    policy, payload = receipt_case
    request = append_lookup(S._discovery_request("996", "17.0"), "996", "saved-id", policy, payload)
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
        + "$rq=Get-Content -Raw -LiteralPath $args[0]\nif(-not [Gate]::Allowed($rq)){throw 'valid stock receipt rejected'}\nforeach($bad in @($rq.Replace('ItemInventoryQueryRq','ItemInventoryAddRq'),$rq.Replace('AverageCost','CreditCardInfo'),$rq.Replace('<ListID>service-id</ListID>','<FullName>service-id</FullName>'))){if([Gate]::Allowed($bad)){throw 'unsafe stock receipt accepted'}}\n"
    )
    r = subprocess.run(
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
    assert r.returncode == 0, r.stderr
