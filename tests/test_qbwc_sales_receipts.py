"""Sales receipt cash accounting, exact readback and query-only recovery."""
# ruff: noqa: F401,F811

import copy
import json
import time
from dataclasses import replace
from decimal import Decimal
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import sales_receipts as sr
from kaydbooks_bridge.config import PERMISSIONS, BridgeError, Config
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.web_ui import check_masters, manual
from test_direct_sdk import direct
from test_invoice_commercial import commercial, response
from test_invoice_compatibility import setup_invoice
from test_invoice_receipt import receipt_case, saved_receipt
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service


@pytest.fixture(params=[False, True], ids=["service", "inventory"])
def sales_case(receipt_case, commercial, request):
    policy, payload = receipt_case
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        payment_masters={
            "customers": {payload["customer_id"]: "customer-id"},
            "receivable": "ar-id",
            "deposits": {"cash": "bank-id"},
            "methods": {"cash": "method-id"},
        },
        sample_sales_receipt_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved bounded synthetic sales receipt testing",
            "ref_prefix": "SYN-",
            "max_receipts": 2,
            "expires_at": time.time() + 3600,
        },
    )
    if request.param:
        next(iter(raw["companies"]["company-a"]["invoice_masters"]["items"].values())).update(
            kind="Inventory", cogs_account_id="cogs-id", asset_account_id="asset-id"
        )
    path.write_text(json.dumps(raw))
    return path, token, {**payload, "deposit_id": "cash", "method_id": "cash"}


def sales_row():
    row = saved_receipt()[0][0][0]
    row.tag = "SalesReceiptRet"
    for node in list(row):
        if node.tag in sr.INVOICE_ONLY or node.tag == "IsToBeEmailed":
            row.remove(node)
        elif node.tag == "InvoiceLineRet":
            node.tag = "SalesReceiptLineRet"
    E.SubElement(row, "TotalAmount").text = "10.00"
    for tag, key in [("DepositToAccountRef", "bank-id"), ("PaymentMethodRef", "method-id")]:
        E.SubElement(E.SubElement(row, tag), "ListID").text = key
    return row


class Session:
    def __init__(self):
        self.saved = []
        self.bank = Decimal("500")
        self.customer = Decimal("25")
        self.writes = 0
        self.method = "Cash"
        self.stock = Decimal("2")
        self.cost = "5.00"

    def xml(self, request):
        root = E.fromstring(request)
        extras = [q for q in root[0] if q.tag == "SalesReceiptQueryRq"]
        for q in extras:
            root[0].remove(q)

        def masters(rows):
            rows[("ItemInventory", "service-id")].update(
                QuantityOnHand=str(self.stock), AverageCost=self.cost, QuantityOnSalesOrder="0"
            )
            rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
            for key in [("Account", "ar-id"), ("Customer", "customer-id")]:
                rows[key].pop("CurrencyRef")
            rows[("Customer", "customer-id")]["Balance"] = str(self.customer)
            rows[("Account", "bank-id")] = {
                "ListID": "bank-id",
                "IsActive": "true",
                "AccountType": "Bank",
                "Balance": str(self.bank),
            }
            rows[("PaymentMethod", "method-id")] = {
                "ListID": "method-id",
                "IsActive": "true",
                "PaymentMethodType": self.method,
            }

        result = E.fromstring(response(E.tostring(root), taxable=False, mutate=masters))
        for q in extras:
            rs = E.SubElement(
                result[0],
                "SalesReceiptQueryRs",
                requestID=q.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            for row in self.saved:
                if all(
                    q.find(k) is None or q.findtext(k) == row.findtext(k)
                    for k in ("TxnID", "RefNumber")
                ):
                    rs.append(copy.deepcopy(row))
            if not len(rs):
                rs.set("statusCode", "500")
                rs.set("statusSeverity", "Warn")
        return E.tostring(result, encoding="unicode")

    def write(self, request):
        self.writes += 1
        row = sales_row()
        self.saved.append(row)
        self.bank += Decimal("10")
        self.stock -= 2
        root = E.Element("QBXML")
        rs = E.SubElement(
            E.SubElement(root, "QBXMLMsgsRs"),
            "SalesReceiptAddRs",
            requestID=E.fromstring(request)[0][0].get("requestID"),
            statusCode="0",
            statusSeverity="Info",
        )
        rs.append(copy.deepcopy(row))
        return E.tostring(root, encoding="unicode")


@pytest.fixture
def queued_sale(sales_case):
    path, token, payload = sales_case
    bridge, sim = Bridge(path), Session()
    args = (bridge, token, "company-a", "sales-receipt.create", "connector-company-a", payload)
    assert check_masters(*args)["pending"]
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    checked = check_masters(*args)
    assert not checked["pending"]
    policy = Config.load(path).companies["company-a"]
    job = manual(
        bridge,
        token,
        "company-a",
        "sale",
        policy.sources[0],
        "sales-receipt.create",
        payload,
        checked["evidence"],
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "10.00"
    bridge.action(token, "company-a", job["id"], "submit")
    bridge.pause(token, "company-a", False)
    return bridge, token, job["id"], sim


@pytest.mark.parametrize("lost", [False, True])
def test_cash_sale_and_lost_response_never_resend(queued_sale, lost):
    bridge, token, job, sim = queued_sale
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    write = send(svc, ticket)
    assert "SalesReceiptAddRq" in write and "InvoiceAddRq" not in write
    answer = sim.write(write)
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        recover(bridge, token, "company-a", job)
        svc = service(bridge)
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    else:
        assert receive(svc, ticket, answer) == 75
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified"
    effects = result["transaction_receipt"]["receipt"]["balance_effects"]
    assert Decimal(effects["deposit_after"]) == Decimal("510")
    assert Decimal(effects["customer_balance_unchanged"]) == Decimal("25")
    assert sim.writes == 1 and bridge.audit(token, "company-a")["valid"]
    with svc._stores["company-a"].transaction() as db:
        assert attempt_count(db, "sales-receipt.create") == 1
        assert attempt_count(db, "invoice.create") == 0
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)


@pytest.mark.parametrize(
    "path,value",
    [
        ("TotalAmount", "11"),
        ("DepositToAccountRef/ListID", "other"),
        ("PaymentMethodRef/ListID", "other"),
        ("SalesReceiptLineRet/Quantity", "3"),
        ("IsPending", "true"),
    ],
)
def test_wrong_saved_sale_is_rejected(sales_case, path, value):
    config, _, payload = sales_case
    policy = Config.load(config).companies["company-a"]
    root = E.Element("QBXML")
    rs = E.SubElement(
        E.SubElement(root, "QBXMLMsgsRs"),
        "SalesReceiptQueryRs",
        requestID="981",
        statusCode="0",
        statusSeverity="Info",
    )
    row = sales_row()
    row.find(path).text = value
    rs.append(row)
    with pytest.raises(BridgeError):
        sr.validate_receipt(E.tostring(root), policy, payload, "981")


@pytest.mark.parametrize("change", ["bank", "customer"])
def test_wrong_accounting_effect_stays_held(queued_sale, change):
    bridge, token, job, sim = queued_sale
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert receive(svc, ticket, sim.write(send(svc, ticket))) == 75
    setattr(sim, change, getattr(sim, change) + Decimal("1"))
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"


def test_check_request_order_and_cash_only_preflight(sales_case):
    path, _, payload = sales_case
    policy = Config.load(path).companies["company-a"]
    payload = {**payload, "check_number": "123"}
    add = E.fromstring(sr.add_request(policy, payload, "981"))[0][0][0]
    tags = [n.tag for n in add]
    assert tags.index("IsPending") < tags.index("CheckNumber") < tags.index("PaymentMethodRef")
    assert tags.index("DepositToAccountRef") < tags.index("SalesReceiptLineAdd")
    lookup = E.fromstring(
        sr.append_query("<QBXML><QBXMLMsgsRq/></QBXML>", "981", txn_id="saved-id")
    )[0][0]
    # Intuit's SalesReceiptQuery schema has no linked-transaction option.
    assert [n.tag for n in lookup][:2] == ["TxnID", "IncludeLineItems"]
    assert all(n.tag == "IncludeRetElement" for n in list(lookup)[2:])
    assert "LinkedTxn" not in [n.text for n in lookup]
    from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S

    check = sr.plan(policy, payload)
    request = sr.append_check(S._discovery_request("981", "17.0"), "981", check)
    sim = Session()
    with pytest.raises(BridgeError):
        sr.validate_check(sim.xml(request), "981", check)

    sim.method = "Check"
    assert sr.validate_check(sim.xml(request), "981", check)[1]["deposit_balance"] == "500"
    sim.method = "CreditCard"
    with pytest.raises(BridgeError):
        sr.validate_check(sim.xml(request), "981", check)


def test_unknown_outcome_is_held_without_second_write(queued_sale):
    bridge, token, job, sim = queued_sale
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert "SalesReceiptAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(bridge, token, "company-a", job)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    assert bridge.status(token, "company-a", job)["state"] == "unknown"
    assert send(svc, ticket) == ""
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)


def test_revoked_authority_before_handoff_prevents_write(queued_sale):
    bridge, token, job, sim = queued_sale
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    raw = json.loads(bridge.config_path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("post-sample")
    bridge.config_path.write_text(json.dumps(raw))
    assert send(svc, ticket) == ""
    assert sim.writes == 0


def test_stale_or_changed_evidence_cannot_prepare(queued_sale):
    from kaydbooks_bridge.sales_receipt_evidence import resolve

    bridge, token, job, sim = queued_sale
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


def test_inventory_cost_or_quantity_mismatch_cannot_verify(sales_case):
    path, _, payload = sales_case
    policy = Config.load(path).companies["company-a"]
    if not sr.plan(policy, payload)["inventory"]:
        return
    before = {
        "deposit_balance": "500",
        "customer_balance": "25",
        "stock": {"service-id": {"quantity_on_hand": "2", "average_cost": "5"}},
    }
    after = {
        "deposit_balance": "510",
        "customer_balance": "25",
        "stock": {"service-id": {"quantity_on_hand": "0", "average_cost": "5"}},
    }
    sr.verify_balance_effect(payload, before, after, policy=policy)
    for field in ("quantity_on_hand", "average_cost"):
        changed = copy.deepcopy(after)
        changed["stock"]["service-id"][field] = "1"
        with pytest.raises(BridgeError):
            sr.verify_balance_effect(payload, before, changed, policy=policy)
