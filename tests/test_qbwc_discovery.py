"""Synthetic QBWC callback tests. No QuickBooks process or company file is used."""

import json
import sqlite3
from pathlib import Path

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.deployment import export_binding_candidate
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.store import Store
from qbwc_kit import soap
from qbwc_kit.qbxml import QBXMLRequest, query
from qbwc_kit.testing import FakeQuickBooks, FakeWebConnector, _result, service_transport

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PASSWORD_A = "synthetic-qbwc-a-" + "a" * 32
PASSWORD_B = "synthetic-qbwc-b-" + "b" * 32

COMPANY_A = {
    "CompanyName": "Synthetic Company A",
    "LegalCompanyName": "Synthetic Company A LLC",
    "EIN": "00-0000001",
}
COMPANY_B = {
    "CompanyName": "Synthetic Company B",
    "LegalCompanyName": "Synthetic Company B LLC",
    "EIN": "00-0000002",
}
HOST = {
    "ProductName": "Synthetic QuickBooks Desktop",
    "MajorVersion": "34",
    "MinorVersion": "0",
    "Country": "US",
    "SupportedQBXMLVersion": ["13.0", "16.0"],
    "QBFileMode": "MultiUser",
}


@pytest.fixture
def discovery_setup(tmp_path, monkeypatch):
    raw = json.loads((EXAMPLES / "company-config.example.json").read_text())
    raw["state_root"] = str(tmp_path / "state")
    path = tmp_path / "private.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("KAYDBOOKS_QBWC_COMPANY_A_SECRET", PASSWORD_A)
    monkeypatch.setenv("KAYDBOOKS_QBWC_COMPANY_B_SECRET", PASSWORD_B)
    monkeypatch.setenv("KAYDBOOKS_QBWC_COMPANY_A_FILE", r"C:\Synthetic\CompanyA.QBW")
    monkeypatch.setenv("KAYDBOOKS_QBWC_COMPANY_B_FILE", r"C:\Synthetic\CompanyB.QBW")
    return path, raw


def call(service, method, **params):
    return _result(service.dispatch(soap.build_request(method, list(params.items()))))


def authenticate(service, connector="connector-company-a", password=PASSWORD_A):
    return call(service, "authenticate", strUserName=connector, strPassword=password)


def send(service, ticket, *, hcp="", company_file=r"C:\Synthetic\CompanyA.QBW"):
    return call(
        service,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse=hcp,
        strCompanyFileName=company_file,
        qbXMLCountry="US",
        qbXMLMajorVers="13",
        qbXMLMinorVers="0",
    )


def response_for(request, company=COMPANY_A):
    return FakeQuickBooks(entities={"Host": [HOST], "Company": [company]})(request)


def hcp_for(company=COMPANY_A):
    request = QBXMLRequest(
        [query("Host"), query("Company"), query("Preferences")], version="13.0"
    ).render()
    return FakeQuickBooks(entities={"Host": [HOST], "Company": [company]})(request)


def receive(service, ticket, response, *, hresult="", message=""):
    return int(
        call(
            service,
            "receiveResponseXML",
            ticket=ticket,
            response=response,
            hresult=hresult,
            message=message,
        )
    )


def test_read_only_company_binding_is_persisted_and_audited(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, company_file = authenticate(service)

    request = send(service, ticket, hcp=hcp_for())
    waiting = service.inspect_session(ticket)
    assert request == waiting["request_xml"]
    assert waiting["state"] == "request-sent"
    assert "HostQueryRq" in request and "CompanyQueryRq" in request
    assert not any(term in request for term in ("AddRq", "ModRq", "DelRq"))
    assert company_file not in request

    assert receive(service, ticket, response_for(request)) == 100
    bound = service.inspect_session(ticket)
    assert bound["state"] == "verified"
    assert (
        bound["identity_hash"] == service.config.connectors["connector-company-a"].identity_sha256
    )
    assert bound["company_file_hash"] and company_file not in json.dumps(bound)

    store = Store(service.config.root, "company-a")
    with store.transaction() as db:
        assert store.verify_audit(db)
        events = [row[0] for row in db.execute("SELECT event FROM audit ORDER BY sequence")]
    assert "qbwc_discovery_request_persisted" in events
    assert "qbwc_company_binding_verified" in events


def test_unconfirmed_binding_captures_hcp_but_never_returns_a_request(discovery_setup):
    path, raw = discovery_setup
    raw["connectors"]["connector-company-a"]["identity_sha256"] = "0" * 64
    path.write_text(json.dumps(raw))
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)

    assert send(service, ticket, hcp=hcp_for()) == ""
    session = service.inspect_session(ticket)
    assert session["state"] == "blocked"
    assert session["hcp_xml"]
    assert session["request_xml"] is None
    assert session["last_error"] == "company binding is not operator-confirmed"

    candidate = path.parent / "binding-candidate.json"
    export_binding_candidate(path, "connector-company-a", candidate)
    evidence = json.loads(candidate.read_text())
    assert evidence["operator_confirmed"] is False
    assert evidence["claims"] == COMPANY_A
    assert evidence["identity_sha256"] != "0" * 64
    assert (
        json.loads(path.read_text())["connectors"]["connector-company-a"]["identity_sha256"]
        == "0" * 64
    )


def test_inherited_fake_connector_runs_the_durable_discovery_cycle(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    connector = FakeWebConnector(
        transport=service_transport(service),
        username="connector-company-a",
        password=PASSWORD_A,
        company_file=r"C:\Synthetic\CompanyA.QBW",
    )

    result = connector.run_update(FakeQuickBooks(entities={"Host": [HOST], "Company": [COMPANY_A]}))

    assert result.authenticated
    assert result.round_trips == 1
    assert result.progress == [100]
    assert result.close_message == "OK"


def test_restart_returns_exact_persisted_request_then_accepts_response(discovery_setup):
    path, _ = discovery_setup
    first = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(first)
    request = send(first, ticket)

    restarted = DurableQBWCDiscoveryService.from_path(path)
    assert send(restarted, ticket) == request
    assert receive(restarted, ticket, response_for(request)) == 100
    assert restarted.inspect_session(ticket)["request_return_count"] == 2


def test_duplicate_callbacks_are_idempotent(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    first_auth = authenticate(service)
    assert authenticate(service) == first_auth
    ticket = first_auth[0]
    request = send(service, ticket)
    assert send(service, ticket) == request
    response = response_for(request)
    assert receive(service, ticket, response) == 100
    assert receive(service, ticket, response) == 100
    assert call(service, "closeConnection", ticket=ticket) == "OK"
    assert call(service, "closeConnection", ticket=ticket) == "OK"

    session = service.inspect_session(ticket)
    assert session["response_callback_count"] == 2
    store = Store(service.config.root, "company-a")
    with store.transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_callbacks WHERE ticket=?", (ticket,)).fetchone()[
                0
            ]
            == 8
        )


def test_expired_ticket_is_held_and_new_session_gets_new_ticket(discovery_setup):
    path, _ = discovery_setup
    now = [100.0]
    service = DurableQBWCDiscoveryService.from_path(path, ttl_seconds=10, clock=lambda: now[0])
    old_ticket, _ = authenticate(service)
    now[0] = 110.0
    assert send(service, old_ticket) == ""
    assert call(service, "getLastError", ticket=old_ticket) == "session expired"
    assert service.inspect_session(old_ticket)["state"] == "expired"
    new_ticket, _ = authenticate(service)
    assert new_ticket != old_ticket


def test_overlapping_connectors_for_one_company_return_busy_then_disconnect_releases(
    discovery_setup, monkeypatch
):
    path, raw = discovery_setup
    raw["connectors"]["alternate-company-a"] = {
        **raw["connectors"]["connector-company-a"],
        "password_env": "KAYDBOOKS_QBWC_ALTERNATE_A_SECRET",
        "company_file_env": "KAYDBOOKS_QBWC_ALTERNATE_A_FILE",
    }
    path.write_text(json.dumps(raw))
    alternate_password = "synthetic-alternate-" + "z" * 32
    monkeypatch.setenv("KAYDBOOKS_QBWC_ALTERNATE_A_SECRET", alternate_password)
    monkeypatch.setenv("KAYDBOOKS_QBWC_ALTERNATE_A_FILE", r"C:\Synthetic\CompanyA.QBW")
    service = DurableQBWCDiscoveryService.from_path(path)
    first_ticket, _ = authenticate(service)

    assert authenticate(service, "alternate-company-a", alternate_password) == ["", "busy"]
    assert (
        call(
            service,
            "connectionError",
            ticket=first_ticket,
            hresult="0x80040408",
            message="synthetic disconnect",
        )
        == "done"
    )
    second_ticket, _ = authenticate(service, "alternate-company-a", alternate_password)
    assert second_ticket and second_ticket != first_ticket
    assert service.inspect_session(first_ticket)["state"] == "disconnected"


def test_cross_session_response_replay_is_blocked(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket_a, _ = authenticate(service)
    request_a = send(service, ticket_a)
    ticket_b, _ = authenticate(service, "connector-company-b", PASSWORD_B)
    request_b = send(service, ticket_b, company_file=r"C:\Synthetic\CompanyB.QBW")

    assert receive(service, ticket_b, response_for(request_a, COMPANY_A)) == -1
    assert "correlation mismatch" in service.inspect_session(ticket_b)["last_error"]
    assert receive(service, ticket_a, response_for(request_a, COMPANY_A)) == 100
    assert request_a != request_b


def test_matching_file_path_cannot_override_company_identity_mismatch(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, configured_file = authenticate(service)
    request = send(service, ticket, company_file=configured_file)

    assert receive(service, ticket, response_for(request, COMPANY_B)) == -1
    session = service.inspect_session(ticket)
    assert session["state"] == "blocked"
    assert session["last_error"] == "configured company binding mismatch"


def test_hcp_mismatch_blocks_before_any_request_is_returned(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)

    assert send(service, ticket, hcp=hcp_for(COMPANY_B)) == ""
    session = service.inspect_session(ticket)
    assert session["state"] == "blocked"
    assert session["request_xml"] is None
    assert session["hcp_xml"] == hcp_for(COMPANY_B)
    assert session["last_error"] == "HCP company binding mismatch"


def test_conflicting_repeated_response_revokes_verified_session(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)
    request = send(service, ticket)
    response = response_for(request)
    assert receive(service, ticket, response) == 100

    assert receive(service, ticket, response.replace("MultiUser", "SingleUser")) == -1
    assert service.inspect_session(ticket)["state"] == "blocked"


def test_ambiguous_identity_hash_across_companies_is_rejected(discovery_setup):
    path, raw = discovery_setup
    raw["connectors"]["connector-company-b"]["identity_sha256"] = raw["connectors"][
        "connector-company-a"
    ]["identity_sha256"]
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="binding is ambiguous"):
        Config.load(path)


def test_one_company_cannot_have_inconsistent_connector_identities(discovery_setup, monkeypatch):
    path, raw = discovery_setup
    raw["connectors"]["alternate-company-a"] = {
        **raw["connectors"]["connector-company-a"],
        "password_env": "KAYDBOOKS_QBWC_ALTERNATE_A_SECRET",
        "company_file_env": "KAYDBOOKS_QBWC_ALTERNATE_A_FILE",
        "identity_sha256": "c" * 64,
    }
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("KAYDBOOKS_QBWC_ALTERNATE_A_SECRET", "synthetic-" + "x" * 32)
    with pytest.raises(BridgeError, match="inconsistent identity bindings"):
        Config.load(path)


def test_missing_identity_claim_is_blocked(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)
    request = send(service, ticket)
    incomplete = {key: value for key, value in COMPANY_A.items() if key != "EIN"}

    assert receive(service, ticket, response_for(request, incomplete)) == -1
    assert (
        service.inspect_session(ticket)["last_error"] == "company identity evidence is incomplete"
    )


def test_multiple_company_records_are_ambiguous_and_blocked(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)
    request = send(service, ticket)
    response = FakeQuickBooks(entities={"Host": [HOST], "Company": [COMPANY_A, COMPANY_B]})(request)

    assert receive(service, ticket, response) == -1
    assert "exactly one Company record" in service.inspect_session(ticket)["last_error"]


def test_display_names_without_a_stronger_claim_are_rejected(discovery_setup):
    path, raw = discovery_setup
    connector = raw["connectors"]["connector-company-a"]
    connector["identity_fields"] = [
        "CompanyName",
        "LegalCompanyName",
        "FirstMonthFiscalYear",
    ]
    path.write_text(json.dumps(raw))

    with pytest.raises(BridgeError, match="supported claims"):
        Config.load(path)


def test_company_query_country_version_minimum_is_enforced(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)
    request = call(
        service,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse="",
        strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
        qbXMLCountry="CA",
        qbXMLMajorVers="1",
        qbXMLMinorVers="0",
    )

    assert request == ""
    assert "unsupported for this country/version" in service.inspect_session(ticket)["last_error"]


def test_persisted_callback_evidence_is_immutable(discovery_setup):
    path, _ = discovery_setup
    service = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(service)
    request = send(service, ticket)
    assert receive(service, ticket, response_for(request)) == 100
    store = Store(service.config.root, "company-a")

    with (
        pytest.raises(sqlite3.IntegrityError, match="response evidence"),
        store.transaction() as db,
    ):
        db.execute("UPDATE qbwc_sessions SET response_xml='<changed/>' WHERE ticket=?", (ticket,))
    with (
        pytest.raises(sqlite3.IntegrityError, match="context is immutable"),
        store.transaction() as db,
    ):
        db.execute("UPDATE qbwc_sessions SET country='CA' WHERE ticket=?", (ticket,))
    with (
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
        store.transaction() as db,
    ):
        db.execute("DELETE FROM qbwc_callbacks WHERE ticket=?", (ticket,))
    with (
        pytest.raises(sqlite3.IntegrityError, match="durable evidence"),
        store.transaction() as db,
    ):
        db.execute("DELETE FROM qbwc_sessions WHERE ticket=?", (ticket,))
