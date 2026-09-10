"""Balanced journal requests, account effects and interrupted-response recovery."""
# ruff: noqa: F401,F811

import copy
import json
import time
from decimal import Decimal
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import journal_entries as journal
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
def journal_case(receipt_case, commercial):
    path, token, _ = commercial
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"] = sorted(PERMISSIONS)
    raw["companies"]["company-a"].update(
        approval_required=False,
        journal_masters={"accounts": {"cash": "bank-id", "office": "expense-id"}},
        sample_journal_posting={
            "connector": "connector-company-a",
            "authorization": "Operator approved bounded synthetic journal testing",
            "ref_prefix": "SYN-",
            "max_entries": 2,
            "expires_at": time.time() + 3600,
        },
    )
    path.write_text(json.dumps(raw))
    payload = {
        "txn_date": "2026-09-07",
        "ref_number": "SYN-JR-001",
        "currency": "USD",
        "memo": "Reviewed adjustment",
        "lines": [
            {"account_id": "office", "side": "debit", "amount": "5.00"},
            {"account_id": "cash", "side": "credit", "amount": "5.00"},
        ],
    }
    return path, token, payload


class Session:
    def __init__(self):
        self.bank = Decimal("500")
        self.expense = Decimal("20")
        self.saved = []
        self.writes = 0

    def xml(self, request):
        root = E.fromstring(request)
        queries = [q for q in root[0] if q.tag == "JournalEntryQueryRq"]
        for q in queries:
            root[0].remove(q)

        def masters(rows):
            rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
            for key, kind, balance in [
                ("bank-id", "Bank", self.bank),
                ("expense-id", "Expense", self.expense),
            ]:
                rows[("Account", key)] = {
                    "ListID": key,
                    "IsActive": "true",
                    "AccountType": kind,
                    "Balance": str(balance),
                }

        result = E.fromstring(response(E.tostring(root), taxable=False, mutate=masters))
        for q in queries:
            rs = E.SubElement(
                result[0],
                "JournalEntryQueryRs",
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
        req = E.fromstring(request)[0][0]
        row = copy.deepcopy(req[0])
        row.tag = "JournalEntryRet"
        E.SubElement(row, "TxnID").text = "journal-id"
        E.SubElement(row, "EditSequence").text = "1234"
        for i, node in enumerate(n for n in row if n.tag.startswith("Journal")):
            E.SubElement(node, "TxnLineID").text = "line-" + str(i)
        self.saved.append(row)
        self.writes += 1
        self.bank -= 5
        self.expense += 5
        root = E.Element("QBXML")
        rs = E.SubElement(
            E.SubElement(root, "QBXMLMsgsRs"),
            "JournalEntryAddRs",
            requestID=req.get("requestID"),
            statusCode="0",
            statusSeverity="Info",
        )
        rs.append(copy.deepcopy(row))
        return E.tostring(root, encoding="unicode")


@pytest.fixture
def queued_journal(journal_case):
    path, token, payload = journal_case
    bridge = Bridge(path)
    sim = Session()
    args = (bridge, token, "company-a", "journal.create", "connector-company-a", payload)
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
        "journal-one",
        policy.sources[0],
        "journal.create",
        payload,
        checked["evidence"],
    )
    bridge.action(token, "company-a", job["id"], "validate")
    assert bridge.preview(token, "company-a", job["id"])["total"] == "5.00"
    bridge.action(token, "company-a", job["id"], "submit")
    bridge.pause(token, "company-a", False)
    return bridge, token, job["id"], sim


@pytest.mark.parametrize("lost", [False, True])
def test_balanced_journal_posts_once_and_recovers(queued_journal, lost):
    b, t, j, sim = queued_journal
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    write = send(svc, ticket)
    assert "JournalEntryAddRq" in write
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
    assert (
        Decimal(effects["bank-id"]["after"]) == 495
        and Decimal(effects["expense-id"]["after"]) == 25
    )
    with svc._stores["company-a"].transaction() as db:
        assert (
            attempt_count(db, "journal.create") == 1
            and attempt_count(db, "sales-receipt.create") == 0
        )
    assert sim.writes == 1 and b.audit(t, "company-a")["valid"]
    with pytest.raises(BridgeError):
        enqueue(b, t, "company-a", j)


@pytest.mark.parametrize(
    "fault",
    ["unbalanced", "negative", "same-account", "unknown", "wrong-currency", "reference", "date"],
)
def test_invalid_journal_is_rejected(journal_case, fault):
    path, _, payload = journal_case
    policy = Config.load(path).companies["company-a"]
    if fault == "unbalanced":
        payload["lines"][1]["amount"] = "4.00"
    elif fault == "negative":
        payload["lines"][0]["amount"] = "-5.00"
    elif fault == "same-account":
        payload["lines"][0]["account_id"] = "cash"
    elif fault == "unknown":
        payload["lines"][0]["account_id"] = "missing"
    elif fault == "wrong-currency":
        payload["currency"] = "EUR"
    elif fault == "reference":
        payload["ref_number"] = "REFERENCE-TOO-LONG"
    else:
        payload["txn_date"] = "tomorrow"
    with pytest.raises(BridgeError):
        journal.add_request(policy, payload, "981")


@pytest.mark.parametrize("fault", ["bank", "expense", "saved-line"])
def test_wrong_journal_effect_is_held(queued_journal, fault):
    b, t, j, sim = queued_journal
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert receive(svc, ticket, sim.write(send(svc, ticket))) == 75
    if fault == "saved-line":
        sim.saved[0].find("JournalDebitLine/Amount").text = "6.00"
    else:
        setattr(sim, fault, getattr(sim, fault) + 1)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    assert b.status(t, "company-a", j)["state"] == "posted-unverified"


def test_unknown_journal_cannot_resend(queued_journal):
    b, t, j, sim = queued_journal
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    assert "JournalEntryAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(b, t, "company-a", j)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == -1
    with pytest.raises(BridgeError):
        enqueue(b, t, "company-a", j)


def test_revoked_authority_before_handoff_prevents_write(queued_journal):
    bridge, token, job, sim = queued_journal
    enqueue(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    raw = json.loads(bridge.config_path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove("post-sample")
    bridge.config_path.write_text(json.dumps(raw))
    assert send(svc, ticket) == ""
    assert sim.writes == 0


def test_stale_or_changed_evidence_cannot_prepare(queued_journal):
    from kaydbooks_bridge.journal_evidence import resolve

    bridge, token, job, sim = queued_journal
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


def test_us_journal_memo_uses_supported_line_fields(journal_case):
    path, _, payload = journal_case
    policy = Config.load(path).companies["company-a"]
    req = E.fromstring(journal.add_request(policy, payload, "981"))[0][0][0]
    assert req.find("Memo") is None
    assert [n.findtext("Memo") for n in req if n.tag.startswith("Journal")] == [payload["memo"]] * 2
    sim = Session()
    response = sim.write(journal.add_request(policy, payload, "981"))
    journal.validate_receipt(response, policy, payload, "981", operation="JournalEntryAdd")
    root = E.fromstring(response)
    root[0][0][0].find("JournalDebitLine").remove(root[0][0][0].find("JournalDebitLine/Memo"))
    with pytest.raises(BridgeError):
        journal.validate_receipt(
            E.tostring(root), policy, payload, "981", operation="JournalEntryAdd"
        )


def test_non_scalar_journal_side_is_validation_error(journal_case):
    path, _, payload = journal_case
    payload["lines"][0]["side"] = ["debit"]
    with pytest.raises(BridgeError):
        journal.validate_payload(payload, Config.load(path).companies["company-a"])
