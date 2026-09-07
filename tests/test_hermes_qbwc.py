"""Hermes uses real QBWC callbacks, including unknown-write query-only recovery."""
# ruff: noqa: F401,F811

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.hermes_tools import Tools
from test_qbwc_journals import (
    authenticate,
    call,
    commercial,
    direct,
    discovery_setup,
    journal_case,
    queued_journal,
    receipt_case,
    receive,
    send,
    service,
    setup_invoice,
)


@pytest.mark.parametrize("lost", [False, True])
def test_hermes_dispatch_and_recover_use_qbwc_only(queued_journal, lost):
    b, t, j, sim = queued_journal
    tools = Tools(b.config_path, t)

    def invoke(action):
        return tools.call(
            "qbwc_entry_v1", "company-a", {"action": action, "parameters": {"job_id": j}}
        )

    with pytest.raises(BridgeError, match="validated job"):
        invoke("preview")
    invoke("dispatch")
    svc = service(b)
    ticket, _ = authenticate(svc)
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 25
    result = sim.write(send(svc, ticket))
    if lost:
        call(svc, "closeConnection", ticket=ticket)
        invoke("recover")
        ticket, _ = authenticate(svc)
        assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 75
    else:
        assert receive(svc, ticket, result) == 75
    assert receive(svc, ticket, sim.xml(send(svc, ticket))) == 100
    assert invoke("status")["state"] == "verified"
    assert sim.writes == 1
    found = tools.call(
        "qbwc_entry_v1",
        "company-a",
        {
            "action": "find",
            "parameters": {
                "operation": "journal.create",
                "ref_number": invoke("status")["payload"]["ref_number"].lower(),
            },
        },
    )
    assert not found["ambiguous"] and not found["posting_performed"]
    assert [(x["id"], x["state"]) for x in found["matches"]] == [(j, "verified")]
    assert sim.writes == 1
    with pytest.raises(BridgeError):
        invoke("dispatch")


@pytest.mark.parametrize(
    "action", ["approve", "pause", "sql", "shell", "post-sample", "mark-verified"]
)
def test_hermes_entry_tool_cannot_grant_itself_authority(queued_journal, action):
    b, t, j, sim = queued_journal
    with pytest.raises(BridgeError, match="action unavailable"):
        Tools(b.config_path, t).call(
            "qbwc_entry_v1", "company-a", {"action": action, "parameters": {"job_id": j}}
        )
    assert sim.writes == 0


def test_hermes_dispatch_preserves_required_approval(queued_journal):
    b, t, j, sim = queued_journal
    raw = json.loads(b.config_path.read_text())
    raw["companies"]["company-a"]["approval_required"] = True
    b.config_path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        Tools(b.config_path, t).call(
            "qbwc_entry_v1", "company-a", {"action": "dispatch", "parameters": {"job_id": j}}
        )
    assert sim.writes == 0


def test_company_and_operation_bounds(queued_journal):
    b, t, j, _ = queued_journal
    tools = Tools(b.config_path, t)
    with pytest.raises(BridgeError):
        tools.call("qbwc_entry_v1", "company-b", {"action": "status", "parameters": {"job_id": j}})
    with pytest.raises(BridgeError, match="selected eight"):
        tools.call(
            "qbwc_entry_v1",
            "company-a",
            {
                "action": "check",
                "parameters": {
                    "operation": "master.change",
                    "connector_id": "connector-company-a",
                    "payload": {},
                },
            },
        )


def test_find_is_owned_scoped_exact_and_read_only(queued_journal):
    b, t, j, sim = queued_journal
    tools = Tools(b.config_path, t)
    ref = tools.call(
        "qbwc_entry_v1", "company-a", {"action": "status", "parameters": {"job_id": j}}
    )["payload"]["ref_number"]

    def find(company="company-a", reference=ref):
        return tools.call(
            "qbwc_entry_v1",
            company,
            {
                "action": "find",
                "parameters": {"operation": "journal.create", "ref_number": reference},
            },
        )

    assert [x["id"] for x in find()["matches"]] == [j]
    assert find(reference="MISSING")["matches"] == []
    with pytest.raises(BridgeError):
        find(company="unassigned-company")
    with pytest.raises(BridgeError, match="exact transaction reference"):
        find(reference="%")
    raw = json.loads(b.config_path.read_text())
    actor = b._context(t, "company-a", "read")[1]
    raw["principals"]["different-owner"] = raw["principals"].pop(actor)
    b.config_path.write_text(json.dumps(raw))
    assert find()["matches"] == []
    assert sim.writes == 0
