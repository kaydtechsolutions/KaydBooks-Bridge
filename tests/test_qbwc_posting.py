"""Real SOAP callback flow with synthetic QuickBooks responses; no accounting writes."""
# ruff: noqa: F401,F811

import json
import sqlite3
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc_posting import DurableQBWCPostingService, enqueue, recover
from kaydbooks_bridge.sample_posting import post
from test_direct_sdk import direct
from test_invoice_commercial import commercial
from test_invoice_commercial import response as commercial_response
from test_invoice_compatibility import setup_invoice
from test_invoice_receipt import receipt_case, saved_receipt
from test_qbwc_discovery import authenticate, call, discovery_setup, hcp_for, receive
from test_qbwc_invoices import send
from test_receipt_lifecycle import receipt_exchange, saved_job
from test_sample_posting import Session, queued


def service(bridge, **kwargs):
    return DurableQBWCPostingService.from_path(bridge.config_path, **kwargs)


def preflight_response(request, *, existing=False):
    captured = []
    Session(existing=existing)(request, None, None, lambda response: captured.append(response))
    return captured[0]


def write_response(request):
    root = saved_receipt("InvoiceAdd")
    root[0][0].set("requestID", ET.fromstring(request)[0][0].get("requestID"))
    return ET.tostring(root, encoding="unicode")


def lookup_response(request, tmp_path, mutate=None):
    path = tmp_path / "lookup.xml"
    receipt_exchange(mutate)(request, path)
    return path.read_text()


def start(queued):
    bridge, token, job_id, _ = queued
    assert enqueue(bridge, token, "company-a", job_id)["detail"] == "waiting_for_web_connector"
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    return bridge, token, job_id, svc, ticket


def preflight_cycle(svc, ticket, existing=False):
    request = send(svc, ticket)
    assert "InvoiceAddRq" not in request
    response = preflight_response(request, existing=existing)
    assert receive(svc, ticket, response) == (75 if existing else 25)
    assert receive(svc, ticket, response) == (75 if existing else 25)
    return request, response


def test_callback_invoice_posts_once_and_independently_reads_back(queued, tmp_path):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    svc = service(bridge)
    request = send(svc, ticket)
    assert "InvoiceAddRq" in request
    answer = write_response(request)
    assert receive(svc, ticket, answer) == 75
    assert receive(svc, ticket, answer) == 75
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"
    svc = service(bridge)
    lookup = send(svc, ticket)
    assert "<TxnID>saved-id</TxnID>" in lookup and "InvoiceAddRq" not in lookup
    assert send(svc, ticket) == lookup
    assert receive(svc, ticket, lookup_response(lookup, tmp_path)) == 100
    assert send(svc, ticket) == ""
    assert call(svc, "closeConnection", ticket=ticket) == "OK"
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified"
    assert result["transaction_receipt"]["reference"]["transport"] == "qbwc-posting"
    assert result["transaction_receipt"]["receipt"]["txn_id"] == "saved-id"
    assert bridge.audit(token, "company-a")["valid"]
    with pytest.raises(BridgeError, match="never resend"):
        enqueue(bridge, token, "company-a", job)
    with pytest.raises(BridgeError, match="never retry"):
        post(bridge, token, "company-a", job)


@pytest.mark.parametrize("lost", ["write-response", "repeated-write", "preflight-response"])
def test_interruption_recovers_by_reads_only(queued, tmp_path, lost):
    bridge, token, job, svc, ticket = start(queued)
    writes = 0
    if lost == "preflight-response":
        assert "InvoiceAddRq" not in send(svc, ticket)
    else:
        preflight_cycle(svc, ticket)
        assert "InvoiceAddRq" in send(svc, ticket)
        writes += 1
        if lost == "repeated-write":
            assert send(service(bridge), ticket) == ""
    call(svc, "closeConnection", ticket=ticket)
    assert bridge.status(token, "company-a", job)["state"] == "unknown"
    recover(bridge, token, "company-a", job)
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)
    assert "InvoiceAddRq" not in request
    result = receive(svc, ticket, preflight_response(request, existing=bool(writes)))
    if writes:
        assert result == 75
        request = send(svc, ticket)
        assert "InvoiceAddRq" not in request
        assert receive(svc, ticket, lookup_response(request, tmp_path)) == 100
        assert bridge.status(token, "company-a", job)["state"] == "verified"
    else:
        assert result == -1 and send(svc, ticket) == ""
        assert bridge.status(token, "company-a", job)["state"] == "unknown"
    with svc._stores["company-a"].transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == writes
        )


def test_matching_preexisting_invoice_uses_readback_without_write(queued, tmp_path):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket, existing=True)
    request = send(svc, ticket)
    assert "InvoiceAddRq" not in request
    assert receive(svc, ticket, lookup_response(request, tmp_path)) == 100
    assert (
        bridge.status(token, "company-a", job)["transaction_receipt"]["bridge_dispatched"] is False
    )


@pytest.mark.parametrize("fault", ["pause", "permission", "policy", "expiry", "stale-preflight"])
def test_current_authority_checked_at_write_handoff(queued, fault):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    path = Path(bridge.config_path)
    data = json.loads(path.read_text())
    if fault == "pause":
        with svc._stores["company-a"].transaction() as db:
            db.execute("UPDATE control SET paused=1")
    elif fault == "permission":
        data["principals"][next(iter(data["principals"]))]["companies"]["company-a"].remove(
            "post-sample"
        )
    elif fault == "policy":
        data["companies"]["company-a"]["sample_posting"]["authorization"] = (
            "Changed controlled sample authorization"
        )
    elif fault == "expiry":
        data["companies"]["company-a"]["sample_posting"]["expires_at"] = 1
    else:
        now = svc.clock()
        svc.clock = lambda: now + 121
    path.write_text(json.dumps(data))
    assert send(svc, ticket) == ""
    with svc._stores["company-a"].transaction() as db:
        assert not db.execute("SELECT 1 FROM qbwc_invoice_steps WHERE phase='write'").fetchone()
    assert bridge.status(token, "company-a", job)["state"] == "unknown"


@pytest.mark.parametrize(
    "fault", ["wrong-company", "version", "no-hcp", "out-of-sequence", "expiry"]
)
def test_invalid_session_never_hands_out_a_write(queued, fault):
    bridge, token, job, svc, ticket = start(queued)
    if fault == "out-of-sequence":
        assert receive(svc, ticket, "<wrong/>") == -1
    elif fault == "expiry":
        now = svc.clock()
        svc.clock = lambda: now + 4000
        assert send(svc, ticket) == ""
    else:
        args = (
            {"hcp": hcp_for().replace("Company A", "Other Company")}
            if fault == "wrong-company"
            else {"version": "16"}
            if fault == "version"
            else {"hcp": ""}
        )
        assert send(svc, ticket, **args) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"


def test_wrong_saved_record_stays_unverified(queued, tmp_path):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    assert receive(svc, ticket, write_response(send(svc, ticket))) == 75
    request = send(svc, ticket)
    answer = lookup_response(
        request, tmp_path, lambda row: row.find("InvoiceRet/RefNumber").__setattr__("text", "wrong")
    )
    assert receive(svc, ticket, answer) == -1
    assert bridge.status(token, "company-a", job)["state"] == "posted-unverified"


def test_dispatch_evidence_is_immutable(queued):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    store = svc._stores["company-a"]
    for table in ("qbwc_invoice_attempts", "qbwc_invoice_steps", "qbwc_invoice_responses"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"), store.transaction() as db:
            db.execute(f"DELETE FROM {table}")


def test_malformed_write_response_retained_without_resend(queued):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    assert "InvoiceAddRq" in send(svc, ticket)
    assert receive(svc, ticket, "<broken") == -1
    assert send(svc, ticket) == ""
    assert bridge.status(token, "company-a", job)["state"] == "unknown"


def test_posting_permission_and_shared_quota_required(queued):
    bridge, token, job, _ = queued
    path = Path(bridge.config_path)
    data = json.loads(path.read_text())
    data["companies"]["company-a"]["sample_posting"]["max_invoices"] = 1
    path.write_text(json.dumps(data))
    enqueue(bridge, token, "company-a", job)
    with service(bridge)._stores["company-a"].transaction() as db:
        assert (
            db.execute(
                "SELECT (SELECT COUNT(*) FROM native_invoice_attempts)+(SELECT COUNT(*) FROM qbwc_invoice_attempts)"
            ).fetchone()[0]
            == 1
        )


def test_browser_invoice_check_uses_qbwc_and_reuses_verified_evidence(queued):
    from kaydbooks_bridge.web_ui import check_masters

    bridge, token, _, envelope = queued
    args = (
        bridge,
        token,
        "company-a",
        "invoice.create",
        "connector-company-a",
        envelope["payload"],
    )
    pending = check_masters(*args)
    assert pending["pending"] and pending["evidence"] is None
    assert check_masters(*args)["result"]["job"] == pending["result"]["job"]
    svc = service(bridge)
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)

    def single(rows):
        rows[("Preferences", None)]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
        for kind, key in (("Account", "ar-id"), ("Customer", "customer-id")):
            rows[(kind, key)].pop("CurrencyRef")

    answer = commercial_response(request, taxable=False, mutate=single)
    assert receive(svc, ticket, answer) == 100
    call(svc, "closeConnection", ticket=ticket)
    finished = check_masters(*args)
    assert not finished["pending"] and finished["evidence"]["transport"] == "qbwc"


def test_web_invoice_post_routes_to_qbwc_without_native_helper(queued):
    from kaydbooks_bridge.web_ui import action

    bridge, token, job, _ = queued
    result = action(bridge, token, "company-a", "post-sample", {"job_id": job})
    assert result["posting_transport"] == "qbwc"


def test_conflicting_callback_cannot_authorize_write(queued):
    bridge, token, job, svc, ticket = start(queued)
    _, answer = preflight_cycle(svc, ticket)
    assert receive(svc, ticket, answer.replace('statusCode="0"', 'statusCode="1"', 1)) == -1
    assert send(svc, ticket) == ""


def test_recovery_cannot_be_changed_into_a_write(queued):
    bridge, token, job, svc, ticket = start(queued)
    preflight_cycle(svc, ticket)
    assert "InvoiceAddRq" in send(svc, ticket)
    call(svc, "closeConnection", ticket=ticket)
    recover(bridge, token, "company-a", job)
    store = svc._stores["company-a"]
    with pytest.raises(sqlite3.IntegrityError, match="phase"), store.transaction() as db:
        db.execute("UPDATE qbwc_invoice_runs SET phase='write' WHERE recovery=1")
    with pytest.raises(sqlite3.IntegrityError, match="recovery"), store.transaction() as db:
        run = db.execute("SELECT id FROM qbwc_invoice_runs WHERE recovery=1").fetchone()[0]
        db.execute("INSERT INTO qbwc_invoice_steps VALUES(?,'write','x','x',0)", (run,))
