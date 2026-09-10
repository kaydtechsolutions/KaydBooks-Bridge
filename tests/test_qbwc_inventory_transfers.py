"""Balanced journal requests, account effects and interrupted-response recovery."""
# ruff: noqa: F401,F811

import copy
import json
import time
from decimal import Decimal
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import inventory_transfers as transfer
from kaydbooks_bridge.config import PERMISSIONS, BridgeError, Config
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.web_ui import check_masters, manual
from test_direct_sdk import direct
from test_invoice_commercial import commercial, response
from test_invoice_compatibility import setup_invoice
from test_invoice_receipt import receipt_case
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service


@pytest.fixture
def transfer_case(receipt_case, commercial):
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        inventory_transfer_masters={
            "sites": {"source": "site-a", "destination": "site-b"},
            "items": {"stock": "stock-id"},
        },
        sample_inventory_transfer_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved bounded synthetic journal testing",
            "ref_prefix": "SYN-",
            "max_transfers": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    payload = {
        "txn_date": "2026-09-07",
        "ref_number": "SYN-IT-001",
        "currency": "USD",
        "memo": "Reviewed stock transfer",
        "from_site_id": "source",
        "to_site_id": "destination",
        "lines": [{"item_id": "stock", "quantity": "2"}],
    }
    return path, token, payload


class Session:
    def __init__(self):
        self.total = Decimal("10")
        self.cost = Decimal("5")
        self.source = Decimal("4")
        self.destination = Decimal("6")
        self.enabled = "true"
        self.serial = "None"
        self.bin = False
        self.saved = []
        self.writes = 0

    def xml(self, request):
        root = E.fromstring(request)
        rest = list(root[0])[2:]
        for node in rest:
            root[0].remove(node)
        result = E.fromstring(response(E.tostring(root), taxable=False))

        def field(parent, name, value):
            E.SubElement(parent, name).text = str(value)

        def ref(parent, name, value):
            field(E.SubElement(parent, name), "ListID", value)

        for q in rest:
            kind = q.tag.removesuffix("QueryRq")
            rs = E.SubElement(
                result[0],
                kind + "QueryRs",
                requestID=q.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            if kind == "TransferInventory":
                for saved in self.saved:
                    if all(
                        q.find(k) is None or q.findtext(k) == saved.findtext(k)
                        for k in ("TxnID", "RefNumber")
                    ):
                        rs.append(copy.deepcopy(saved))
                if not len(rs):
                    rs.set("statusCode", "500")
                    rs.set("statusSeverity", "Warn")
                continue
            row = E.SubElement(rs, kind + "Ret")
            if kind == "Preferences":
                field(
                    E.SubElement(row, "MultiLocationInventoryPreferences"),
                    "IsMultiLocationInventoryEnabled",
                    self.enabled,
                )
                inv = E.SubElement(row, "ItemsAndInventoryPreferences")
                field(inv, "IsTrackingSerialOrLotNumber", self.serial)
                field(inv, "FIFOEnabled", "false")
                field(E.SubElement(row, "MultiCurrencyPreferences"), "IsMultiCurrencyOn", "false")
            elif kind == "ItemSites":
                item = q.findtext("ItemSiteFilter/ItemFilter/ListID")
                site = q.findtext("ItemSiteFilter/SiteFilter/ListID")
                field(row, "ListID", item + "-" + site)
                ref(row, "ItemInventoryRef", item)
                ref(row, "InventorySiteRef", site)
                if self.bin:
                    ref(row, "InventorySiteLocationRef", "bin-a")
                field(row, "QuantityOnHand", self.source if site == "site-a" else self.destination)
            else:
                field(row, "ListID", q.findtext("ListID"))
                field(row, "IsActive", "true")
                if kind == "ItemInventory":
                    field(row, "QuantityOnHand", self.total)
                    field(row, "AverageCost", self.cost)
        return E.tostring(result, encoding="unicode")

    def write(self, request):
        req = E.fromstring(request)[0][0]
        row = copy.deepcopy(req[0])
        row.tag = "TransferInventoryRet"
        E.SubElement(row, "TxnID").text = "transfer-id"
        E.SubElement(row, "EditSequence").text = "1234"
        for i, line in enumerate(row.findall("TransferInventoryLineAdd")):
            line.tag = "TransferInventoryLineRet"
            line.find("QuantityToTransfer").tag = "QuantityTransferred"
            E.SubElement(line, "TxnLineID").text = "line-" + str(i)
        self.saved.append(row)
        self.source -= 2
        self.destination += 2
        self.writes += 1
        root = E.Element("QBXML")
        rs = E.SubElement(
            E.SubElement(root, "QBXMLMsgsRs"),
            "TransferInventoryAddRs",
            requestID=req.get("requestID"),
            statusCode="0",
            statusSeverity="Info",
        )
        rs.append(copy.deepcopy(row))
        return E.tostring(root, encoding="unicode")


@pytest.fixture
def queued_transfer(transfer_case):
    path, token, payload = transfer_case
    bridge = Bridge(path)
    sim = Session()
    args = (bridge, token, "company-a", "inventory-transfer.create", "connector-company-a", payload)
    assert check_masters(*args)["pending"]
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    checked = check_masters(*args)
    policy = Config.load(path).companies["company-a"]
    job = manual(
        bridge,
        token,
        "company-a",
        "transfer-one",
        policy.sources[0],
        "inventory-transfer.create",
        payload,
        checked["evidence"],
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "10.00"
    bridge.action(token, "company-a", job["id"], "submit")
    bridge.pause(token, "company-a", False)
    return bridge, token, job["id"], sim


@pytest.mark.parametrize("lost", [False, True])
def test_stock_transfer_posts_once_and_recovers(queued_transfer, lost):
    b, t, j, sim = queued_transfer
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    write = send(svc, ticket)
    assert "TransferInventoryAddRq" in write
    answer = sim.write(write)
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        recover(b, t, "company-a", j)
        svc = service(b)
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    else:
        assert receive(svc, ticket, answer) == 75
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = b.status(t, "company-a", j)
    assert result["state"] == "verified"
    effects = result["transaction_receipt"]["receipt"]["balance_effects"]
    assert effects["stock-id"]["before"] == {"site-a": "4", "site-b": "6"}
    assert effects["stock-id"]["after"] == {"site-a": "2", "site-b": "8"}
    assert effects["stock-id"]["company_stock_unchanged"] == {"quantity": "10", "average_cost": "5"}
    with svc._stores["company-a"].transaction() as db:
        assert (
            attempt_count(db, "inventory-transfer.create") == 1
            and attempt_count(db, "sales-receipt.create") == 0
        )
    assert sim.writes == 1 and b.audit(t, "company-a")["valid"]
    with pytest.raises(BridgeError):
        enqueue(b, t, "company-a", j)


@pytest.mark.parametrize("fault", ["source", "destination", "total", "cost", "saved-line"])
def test_wrong_stock_effect_held(queued_transfer, fault):
    b, t, j, sim = queued_transfer
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert receive(svc, ticket, sim.write(send(svc, ticket))) == 75
    if fault == "saved-line":
        sim.saved[0].find("TransferInventoryLineRet/QuantityTransferred").text = "3"
    else:
        setattr(sim, fault, getattr(sim, fault) + 1)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    assert b.status(t, "company-a", j)["state"] == "posted-unverified"


@pytest.mark.parametrize("fault", ["disabled", "serial", "bin", "sold-out"])
def test_unsupported_transfer_prevents_write(queued_transfer, fault):
    b, t, j, sim = queued_transfer
    enqueue(b, t, "company-a", j)
    if fault == "disabled":
        sim.enabled = "false"
    elif fault == "serial":
        sim.serial = "SerialNumber"
    elif fault == "bin":
        sim.bin = True
    else:
        sim.source = Decimal("1")
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    assert sim.writes == 0 and send(svc, ticket) == ""


@pytest.mark.parametrize(
    "fault",
    [
        "same-site",
        "unknown-site",
        "unknown-item",
        "duplicate-item",
        "zero",
        "negative",
        "precision",
        "date",
    ],
)
def test_invalid_transfer_rejected(transfer_case, fault):
    path, _, payload = transfer_case
    policy = Config.load(path).companies["company-a"]
    if fault == "same-site":
        payload["to_site_id"] = "source"
    elif fault == "unknown-site":
        payload["from_site_id"] = "unknown"
    elif fault == "unknown-item":
        payload["lines"][0]["item_id"] = "unknown"
    elif fault == "duplicate-item":
        payload["lines"] *= 2
    elif fault == "date":
        payload["txn_date"] = "not-a-date"
    else:
        payload["lines"][0]["quantity"] = {"zero": "0", "negative": "-1", "precision": "1.0000001"}[
            fault
        ]
    with pytest.raises(BridgeError):
        transfer.add_request(policy, payload, "981")


def test_unknown_transfer_cannot_resend(queued_transfer):
    b, t, j, sim = queued_transfer
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert "TransferInventoryAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(b, t, "company-a", j)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    with pytest.raises(BridgeError):
        enqueue(b, t, "company-a", j)


def test_revoked_authority_before_handoff_prevents_write(queued_transfer):
    bridge, token, job, sim = queued_transfer
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    raw = json.loads(bridge.config_path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("post-sample")
    bridge.config_path.write_text(json.dumps(raw))
    assert send(svc, ticket) == ""
    assert sim.writes == 0


def test_stale_or_changed_evidence_cannot_prepare(queued_transfer):
    from kaydbooks_bridge.inventory_transfer_evidence import resolve

    bridge, token, job, sim = queued_transfer
    saved = bridge.status(token, "company-a", job)
    config, actor, policy, store = bridge._context(token, "company-a", "read")
    with store.transaction() as db:
        for payload, now in (
            (saved["payload"], bridge.clock() + 901),
            ({**saved["payload"], "ref_number": "OTHER"}, bridge.clock()),
        ):
            with pytest.raises(BridgeError):
                resolve(
                    config,
                    policy,
                    store,
                    db,
                    actor,
                    payload,
                    saved["master_evidence"]["reference"],
                    now,
                )


def test_site_catalog_is_owned_read_only_and_cannot_post(transfer_case):
    from kaydbooks_bridge.qbwc_contracts import contract
    from kaydbooks_bridge.qbwc_invoices import invoice_job

    path, token, _ = transfer_case
    b = Bridge(path)
    svc = service(b)
    args = (svc, token, "connector-company-a", "sites-one")
    assert (
        invoice_job(*args, payload={}, enqueue=True, operation="inventory-sites.read")["state"]
        == "queued"
    )
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)
    assert "InventorySiteQueryRq" in request and "AddRq" not in request
    root = E.fromstring(Session().xml(request))
    site = root[0][-1][0]
    site.find("ListID").text = "site-a"
    E.SubElement(site, "Name").text = "Warehouse A"
    assert receive(svc, ticket, E.tostring(root, encoding="unicode")) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = invoice_job(*args, operation="inventory-sites.read")
    assert result["read_only"] and result["sites"] == [
        {"list_id": "site-a", "name": "Warehouse A", "default": False}
    ]
    with pytest.raises(BridgeError):
        contract("inventory-sites.read")
    assert b.audit(token, "company-a")["valid"]


def test_empty_destination_is_zero_but_empty_source_cannot_post(transfer_case):
    from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService

    path, _, payload = transfer_case
    policy = Config.load(path).companies["company-a"]
    check = transfer.plan(policy, payload)
    request = transfer.append_check(
        DurableQBWCDiscoveryService._discovery_request("981", "17.0"), "981", check
    )
    xml = Session().xml(request)
    for site, allowed in (("site-b", True), ("site-a", False)):
        root = E.fromstring(xml)
        row = next(
            n
            for n in root[0]
            if n.tag == "ItemSitesQueryRs"
            and n.findtext("ItemSitesRet/InventorySiteRef/ListID") == site
        )
        row.remove(row[0])
        row.set("statusCode", "1")
        if allowed:
            balances = transfer.validate_check(E.tostring(root), "981", check)[1]
            assert balances["sites"]["stock-id"][site] == "0"
        else:
            with pytest.raises(BridgeError, match="insufficient stock"):
                transfer.validate_check(E.tostring(root), "981", check)
