"""Synthetic transport/recovery tests; no QuickBooks process is used."""

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.direct_sdk import company_lock, discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from qbwc_kit.testing import FakeQuickBooks
from test_qbwc_discovery import (  # noqa: F401
    COMPANY_A,
    COMPANY_B,
    HOST,
    PASSWORD_A,
    authenticate,
    discovery_setup,
)


@pytest.fixture
def direct(discovery_setup, monkeypatch):  # noqa: F811
    path, raw = discovery_setup
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = ["read", "recover"]
    path.write_text(json.dumps(raw))
    token = "synthetic-operator-" + "x" * 32
    monkeypatch.setenv(raw["principals"][actor]["token_env"], token)
    return path, token


def transport(company=COMPANY_A):
    host = {**HOST, "SupportedQBXMLVersion": ["17.0"]}

    def exchange(request, path):
        path.write_text(FakeQuickBooks(entities={"Host": [host], "Company": [company]})(request))

    return exchange


def run(direct, exchange, **kwargs):
    path, token = direct
    return discover(
        DurableQBWCDiscoveryService.from_path(path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1234",
        exchange=exchange,
        **kwargs,
    )


def test_restart_uses_saved_response_without_external_query(direct):
    def crash(request, path):
        transport()(request, path)
        raise RuntimeError("simulated termination after response save")

    with pytest.raises(RuntimeError):
        run(direct, crash)
    assert run(direct, lambda *_: pytest.fail("must not repeat query"))["state"] == "verified"
    assert run(direct, lambda *_: pytest.fail("duplicate must not query"))["state"] == "verified"


def test_unknown_read_requires_explicit_recovery(direct):
    def disconnect(*_):
        raise RuntimeError("disconnect")

    with pytest.raises(RuntimeError):
        run(direct, disconnect)
    with pytest.raises(BridgeError, match="explicit read recovery"):
        run(direct, transport())
    assert run(direct, transport(), recover_read=True)["state"] == "verified"


def test_mismatch_blocks_without_retry(direct):
    with pytest.raises(BridgeError, match="validation"):
        run(direct, transport(COMPANY_B))
    with pytest.raises(BridgeError, match="blocked"):
        run(direct, transport())


def test_both_transports_block_overlap(direct):
    path, token = direct
    svc = DurableQBWCDiscoveryService.from_path(path)
    ticket, _ = authenticate(svc)
    with pytest.raises(BridgeError, match="QBWC company session"):
        run(direct, transport())
    from qbwc_kit import soap

    svc._do_closeConnection(soap.SoapCall("closeConnection", {"ticket": ticket}, ("ticket",)))

    def exchange(request, destination):
        assert authenticate(svc) == ["", "busy"]
        with (
            pytest.raises(BridgeError, match="busy"),
            company_lock(svc._stores["company-a"].path.with_suffix(".sdk.lock")),
        ):
            pass
        transport()(request, destination)

    assert run(direct, exchange)["state"] == "verified"


def test_authentication_precedes_io(direct):
    path, _ = direct
    with pytest.raises(BridgeError, match="authentication"):
        discover(
            DurableQBWCDiscoveryService.from_path(path),
            "wrong",
            "connector-company-a",
            PASSWORD_A,
            "99",
            exchange=lambda *_: pytest.fail("unauthorized IO"),
        )


def test_company_permission_and_unconfirmed_binding_precede_io(direct):
    path, _ = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = []
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        run(direct, lambda *_: pytest.fail("unauthorized IO"))
    raw["principals"][actor]["companies"]["company-a"] = ["read"]
    raw["connectors"]["connector-company-a"]["identity_sha256"] = "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="operator-confirmed"):
        run(direct, lambda *_: pytest.fail("unconfirmed IO"))


def test_explicit_recovery_requires_recover_permission(direct):
    path, _ = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = ["read"]
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        run(direct, lambda *_: pytest.fail("unauthorized recovery"), recover_read=True)


def test_terminal_response_is_immutable_and_audited(direct):
    import sqlite3

    assert run(direct, transport())["state"] == "verified"
    store = DurableQBWCDiscoveryService.from_path(direct[0])._stores["company-a"]
    with store.transaction() as db:
        assert store.verify_audit(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE sdk_discovery SET response='replacement'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM sdk_discovery")


ACCOUNT = {
    "ListID": "synthetic-account-1",
    "FullName": "Synthetic Sales",
    "AccountType": "Income",
    "IsActive": "true",
}


def account_transport(records=None, company=COMPANY_A):
    def exchange(request, path):
        host = {**HOST, "SupportedQBXMLVersion": ["17.0"]}
        path.write_text(
            FakeQuickBooks(
                entities={
                    "Host": [host],
                    "Company": [company],
                    "Account": [ACCOUNT] if records is None else records,
                }
            )(request)
        )

    return exchange


def test_account_preview_recovers_and_prevents_operation_change(direct):
    def crash(request, path):
        account_transport()(request, path)
        raise RuntimeError("saved then interrupted")

    with pytest.raises(RuntimeError):
        run(direct, crash, accounts=True)
    result = run(direct, lambda *_: pytest.fail("replayed"), accounts=True)
    assert result["accounts"] == [ACCOUNT]
    assert result["complete"] is False
    with pytest.raises(BridgeError, match="ownership"):
        run(direct, transport())


@pytest.mark.parametrize(
    "records",
    [
        [ACCOUNT] * 21,
        [ACCOUNT, ACCOUNT],
        [{**ACCOUNT, "IsActive": "false"}],
        [{"ListID": "missing-fields"}],
    ],
)
def test_account_preview_invalid_records_block(direct, records):
    with pytest.raises(BridgeError, match="validation"):
        run(direct, account_transport(records), accounts=True)


def test_account_preview_wrong_company_never_returns_records(direct):
    with pytest.raises(BridgeError, match="validation"):
        run(direct, account_transport(company=COMPANY_B), accounts=True)


def test_account_preview_projection_excludes_extra_data(direct):
    result = run(
        direct, account_transport([{**ACCOUNT, "BankNumber": "synthetic-private"}]), accounts=True
    )
    assert result["accounts"] == [ACCOUNT]


@pytest.mark.parametrize("replacement", ["999", "12345"])
def test_account_correlation_rejects_foreign_response(direct, replacement):
    def wrong(request, path):
        account_transport()(request, path)
        path.write_text(
            path.read_text().replace('requestID="12343"', 'requestID="' + replacement + '"')
        )

    with pytest.raises(BridgeError, match="validation"):
        run(direct, wrong, accounts=True)
