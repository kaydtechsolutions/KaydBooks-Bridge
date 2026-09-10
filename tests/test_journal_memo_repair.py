"""Memo-only repair retains the original journal and never repeats its Add."""
# ruff: noqa: F401,F811

import copy
import json
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge import journal_memo_repair as repair
from kaydbooks_bridge.config import BridgeError
from test_qbwc_journals import (
    authenticate,
    call,
    commercial,
    direct,
    discovery_setup,
    enqueue,
    journal,
    journal_case,
    queued_journal,
    receipt_case,
    receive,
    recover,
    send,
    service,
    setup_invoice,
)


@pytest.fixture
def held(queued_journal, monkeypatch):
    b, t, j, sim = queued_journal
    original = journal.add_request

    def old(*args, **kwargs):
        root = E.fromstring(original(*args, **kwargs))
        add = root[0][0][0]
        for line in list(add):
            if line.tag in ("JournalDebitLine", "JournalCreditLine"):
                line.remove(line.find("Memo"))
        node = E.Element("Memo")
        node.text = "Reviewed adjustment"
        add.insert(2, node)
        return journal.render(root)

    monkeypatch.setattr(journal, "add_request", old)
    enqueue(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    answer = E.fromstring(sim.write(send(svc, ticket)))
    sim.saved[0].remove(sim.saved[0].find("Memo"))
    answer[0][0][0].remove(answer[0][0][0].find("Memo"))
    answer[0][0].set("statusCode", "530")
    answer[0][0].set("statusSeverity", "Warn")
    assert receive(svc, ticket, E.tostring(answer, encoding="unicode")) == -1
    call(svc, "closeConnection", ticket=ticket)
    monkeypatch.setattr(journal, "add_request", original)
    b.pause(t, "company-a", True)
    path = b.config_path
    raw = json.loads(path.read_text())
    raw["principals"]["memo-reviewer"] = {
        "token_env": "KAYDBOOKS_SYNTHETIC_MEMO_REVIEWER",
        "companies": {"company-a": ["approve"]},
    }
    path.write_text(json.dumps(raw))
    reviewer = "synthetic-memo-reviewer-" + "r" * 32
    monkeypatch.setenv("KAYDBOOKS_SYNTHETIC_MEMO_REVIEWER", reviewer)
    original_xml = sim.xml

    def sample_xml(request):
        root = E.fromstring(original_xml(request))
        company = root.find("QBXMLMsgsRs/CompanyQueryRs/CompanyRet")
        if company is not None:
            E.SubElement(company, "IsSampleCompany").text = "true"
        return E.tostring(root, encoding="unicode")

    sim.xml = sample_xml
    return b, t, j, sim, reviewer


def start(held):
    b, t, j, sim, reviewer = held
    repair.enqueue(b, t, "company-a", j, approver_token=reviewer)
    svc = service(b)
    ticket, _ = authenticate(svc)
    return svc, ticket


def modify(sim, request):
    root = E.fromstring(request)
    rq = root[0][0]
    assert rq.tag == "JournalEntryModRq"
    mod = rq[0]
    assert [n.tag for n in mod] == ["TxnID", "EditSequence", "JournalLineMod", "JournalLineMod"]
    row = sim.saved[0]
    assert mod.findtext("TxnID") == row.findtext("TxnID")
    assert mod.findtext("EditSequence") == row.findtext("EditSequence")
    lines = {n.findtext("TxnLineID"): n for n in row if n.tag.startswith("Journal")}
    assert {n.findtext("TxnLineID") for n in mod.findall("JournalLineMod")} == set(lines)
    for n in mod.findall("JournalLineMod"):
        assert [c.tag for c in n] == ["TxnLineID", "Memo"]
        E.SubElement(lines[n.findtext("TxnLineID")], "Memo").text = n.findtext("Memo")
    row.find("EditSequence").text = "1235"
    root = E.Element("QBXML")
    rs = E.SubElement(
        E.SubElement(root, "QBXMLMsgsRs"),
        "JournalEntryModRs",
        requestID=rq.get("requestID"),
        statusCode="0",
        statusSeverity="Info",
    )
    rs.append(copy.deepcopy(row))
    return E.tostring(root, encoding="unicode")


@pytest.mark.parametrize("lost", [False, True])
def test_memo_only_repair_and_lost_response_reconciliation(held, lost):
    b, t, j, sim, reviewer = held
    svc, ticket = start(held)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    answer = modify(sim, send(svc, ticket))
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        recover(b, t, "company-a", j)
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    else:
        assert receive(svc, ticket, answer) == 75
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    call(svc, "closeConnection", ticket=ticket)
    assert b.status(t, "company-a", j)["state"] == "verified"
    assert sim.writes == 1 and sim.bank == 495 and sim.expense == 25
    assert b.audit(t, "company-a")["valid"]
    with pytest.raises(BridgeError):
        repair.enqueue(b, t, "company-a", j, approver_token=reviewer)
    with svc._stores["company-a"].transaction() as db:
        rows = db.execute("SELECT request FROM qbwc_invoice_steps WHERE phase='write'").fetchall()
        assert len(rows) == 2
        assert sum("JournalEntryAddRq" in r[0] for r in rows) == 1
        assert sum("JournalEntryModRq" in r[0] for r in rows) == 1


@pytest.mark.parametrize("fault", ["company", "amount", "line-id", "memo", "balance"])
def test_changed_accounting_or_identity_refuses_repair(held, fault):
    b, t, j, sim, _ = held
    svc, ticket = start(held)
    root = E.fromstring(sim.xml(send(svc, ticket)))
    row = root.find(".//JournalEntryRet")
    if fault == "company":
        root.find(".//IsSampleCompany").text = "false"
    elif fault == "amount":
        row.find("JournalDebitLine/Amount").text = "6.00"
    elif fault == "line-id":
        row.find("JournalDebitLine/TxnLineID").text = "other-line"
    elif fault == "memo":
        E.SubElement(row.find("JournalDebitLine"), "Memo").text = "Someone edited this"
    else:
        root.find(".//AccountRet/Balance").text = "999"
    assert receive(svc, ticket, E.tostring(root, encoding="unicode")) == -1
    assert not send(svc, ticket)
    assert b.status(t, "company-a", j)["state"] == "unknown"
    assert sim.writes == 1


def test_same_reviewer_refused_and_normal_recovery_never_modifies(held):
    b, t, j, sim, _ = held
    with pytest.raises(BridgeError, match="separate approval"):
        repair.enqueue(b, t, "company-a", j, approver_token=t)
    recover(b, t, "company-a", j)
    svc = service(b)
    ticket, _ = authenticate(svc)
    query = send(svc, ticket)
    assert "ModRq" not in query and "AddRq" not in query
    assert receive(svc, ticket, sim.xml(query)) == -1
    assert not send(svc, ticket)


@pytest.mark.parametrize("fault", ["unpaused", "revoked", "expired"])
def test_repair_authority_rechecked_before_mod_handoff(held, fault):
    b, t, j, sim, _ = held
    svc, ticket = start(held)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    if fault == "unpaused":
        b.pause(t, "company-a", False)
    elif fault == "revoked":
        path = b.config_path
        raw = json.loads(path.read_text())
        raw["principals"]["memo-reviewer"]["companies"]["company-a"] = []
        path.write_text(json.dumps(raw))
    else:
        now = svc.clock()
        svc.clock = lambda: now + 601
    assert not send(svc, ticket)
    assert sim.writes == 1


def test_already_correct_memos_verify_without_modification(held):
    b, t, j, sim, _ = held
    for line in sim.saved[0]:
        if line.tag in ("JournalDebitLine", "JournalCreditLine"):
            E.SubElement(line, "Memo").text = "Reviewed adjustment"
    svc, ticket = start(held)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    assert b.status(t, "company-a", j)["state"] == "verified"
    with svc._stores["company-a"].transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("fault", ["replayed-handoff", "stale-edit-sequence"])
def test_uncertain_or_rejected_mod_is_never_resent(held, fault):
    b, t, j, sim, reviewer = held
    svc, ticket = start(held)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    request = send(svc, ticket)
    if fault == "stale-edit-sequence":
        root = E.Element("QBXML")
        E.SubElement(
            E.SubElement(root, "QBXMLMsgsRs"),
            "JournalEntryModRs",
            requestID=E.fromstring(request)[0][0].get("requestID"),
            statusCode="3200",
            statusSeverity="Error",
        )
        assert receive(svc, ticket, E.tostring(root, encoding="unicode")) == -1
    assert not send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    with pytest.raises(BridgeError, match="already authorized"):
        repair.enqueue(b, t, "company-a", j, approver_token=reviewer)
    assert b.status(t, "company-a", j)["state"] == "unknown"
    assert sim.writes == 1
