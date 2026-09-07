"""QBWC invoice-check callbacks use the same synthetic master responses as SDK."""

import json
import sqlite3

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.qbwc_accounts import account_job
from kaydbooks_bridge.qbwc_invoices import invoice_job
from test_direct_sdk import direct  # noqa: F401
from test_invoice_compatibility import exchange, setup_invoice  # noqa: F401
from test_qbwc_discovery import authenticate, call, discovery_setup, hcp_for, receive  # noqa: F401


def send(svc, ticket, hcp=None, version="17"):
    return call(
        svc,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse=hcp_for() if hcp is None else hcp,
        strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
        qbXMLCountry="US",
        qbXMLMajorVers=version,
        qbXMLMinorVers="0",
    )


def queue(setup):
    path, token, payload = setup
    svc = DurableQBWCDiscoveryService.from_path(path)
    assert (
        invoice_job(svc, token, "connector-company-a", "check-one", payload=payload, enqueue=True)[
            "state"
        ]
        == "queued"
    )
    return svc


def result(setup, svc):
    return invoice_job(svc, setup[1], "connector-company-a", "check-one")


def response_for(request, tmp_path, mutate=None):
    file = tmp_path / "response.xml"
    exchange(mutate)(request, file)
    return file.read_text()


@pytest.mark.parametrize("single", [True, False])
def test_realistic_cycle_restart_repeats_and_one_shot(setup_invoice, tmp_path, single):  # noqa: F811
    path, token, payload = setup_invoice
    if single:
        raw = json.loads(path.read_text())
        raw["companies"]["company-a"]["invoice_masters"]["currency_id"] = None
        path.write_text(json.dumps(raw))
    svc = queue(setup_invoice)
    assert (
        invoice_job(svc, token, "connector-company-a", "check-one", payload=payload, enqueue=True)[
            "state"
        ]
        == "queued"
    )
    ticket, _ = authenticate(svc)
    request = send(svc, ticket)
    svc = DurableQBWCDiscoveryService.from_path(path)
    assert send(svc, ticket) == request

    def mutate(rows):
        if single:
            rows[2]["MultiCurrencyPreferences"] = {"IsMultiCurrencyOn": "false"}
            rows[4].pop("CurrencyRef")
            rows[5].pop("CurrencyRef")

    response = response_for(request, tmp_path, mutate)
    assert receive(svc, ticket, response) == 100
    assert receive(svc, ticket, response) == 100
    assert call(svc, "closeConnection", ticket=ticket) == "OK"
    checked = result(setup_invoice, DurableQBWCDiscoveryService.from_path(path))
    assert checked["compatibility"] == "matched" and not checked["live_posting"]
    assert checked["currency_basis"] == (
        "configured-single-currency" if single else "verified-home-currency"
    )
    with svc._stores["company-a"].transaction() as db:
        assert svc._stores["company-a"].verify_audit(db)
    next_ticket, _ = authenticate(svc)
    assert "CustomerQueryRq" not in send(svc, next_ticket)


@pytest.mark.parametrize("stage", ["before-send", "after-send", "after-response"])
@pytest.mark.parametrize("change", ["permission", "policy"])
def test_changed_context_blocks_callbacks_and_results(setup_invoice, tmp_path, stage, change):  # noqa: F811
    svc = queue(setup_invoice)
    ticket, _ = authenticate(svc)
    if stage != "before-send":
        request = send(svc, ticket)
        response = response_for(request, tmp_path)
        if stage == "after-response":
            assert receive(svc, ticket, response) == 100
    path = setup_invoice[0]
    raw = json.loads(path.read_text())
    if change == "permission":
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "validate"
        )
    else:
        raw["companies"]["company-a"]["invoice_masters"]["currency_id"] = "other-currency"
    path.write_text(json.dumps(raw))
    svc = DurableQBWCDiscoveryService.from_path(path)
    if stage == "before-send":
        assert send(svc, ticket) == ""
    else:
        assert receive(svc, ticket, response) == -1
    with pytest.raises(BridgeError):
        result(setup_invoice, svc)


@pytest.mark.parametrize("first", ["invoice", "account"])
def test_read_queue_exclusion(setup_invoice, first):  # noqa: F811
    path, token, payload = setup_invoice
    svc = DurableQBWCDiscoveryService.from_path(path)

    def invoice():
        invoice_job(svc, token, "connector-company-a", "check-one", payload=payload, enqueue=True)

    def account():
        account_job(svc, token, "connector-company-a", "preview", enqueue=True)

    if first == "invoice":
        invoice()
        with pytest.raises(BridgeError, match="queued"):
            account()
    else:
        account()
        with pytest.raises(BridgeError, match="queued"):
            invoice()


@pytest.mark.parametrize(
    "case", ["missing-hcp", "old-version", "wrong-currency", "wrong-company", "inactive-item"]
)
def test_bad_evidence_or_capability_blocks(setup_invoice, tmp_path, case):  # noqa: F811
    svc = queue(setup_invoice)
    ticket, _ = authenticate(svc)
    request = send(
        svc,
        ticket,
        hcp="" if case == "missing-hcp" else None,
        version="13" if case == "old-version" else "17",
    )
    if case in ("missing-hcp", "old-version"):
        assert request == ""
    else:

        def mutate(rows):
            if case == "wrong-currency":
                rows[5]["CurrencyRef"]["ListID"] = "other"
            elif case == "wrong-company":
                rows[1]["EIN"] = "other"
            else:
                rows[6]["IsActive"] = "false"

        assert receive(svc, ticket, response_for(request, tmp_path, mutate)) == -1
    assert "compatibility" not in result(setup_invoice, svc)


def test_job_context_and_assignment_are_immutable(setup_invoice):  # noqa: F811
    svc = queue(setup_invoice)
    path, token, payload = setup_invoice
    changed = json.loads(json.dumps(payload))
    changed["lines"][0]["amount"] = "2.00"
    with pytest.raises(BridgeError, match="immutable"):
        invoice_job(svc, token, "connector-company-a", "check-one", payload=changed, enqueue=True)
    ticket, _ = authenticate(svc)
    with svc._stores["company-a"].transaction() as db:
        for sql in (
            "UPDATE qbwc_invoice_jobs SET payload='{}'",
            "UPDATE qbwc_invoice_jobs SET ticket=NULL",
            "DELETE FROM qbwc_invoice_jobs",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)
    assert (
        call(svc, "connectionError", ticket=ticket, hresult="0x80040408", message="synthetic")
        == "done"
    )
    next_ticket, _ = authenticate(svc)
    assert "CustomerQueryRq" not in send(svc, next_ticket)
    assert result(setup_invoice, svc)["state"] == "disconnected"
