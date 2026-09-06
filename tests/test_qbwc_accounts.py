"""Synthetic callbacks; actual Web Connector qualification is recorded separately."""

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.qbwc_accounts import account_job
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import ACCOUNT, direct  # noqa: F401
from test_qbwc_discovery import (  # noqa: F401
    COMPANY_A,
    COMPANY_B,
    HOST,
    authenticate,
    call,
    discovery_setup,
    hcp_for,
    receive,
    send,
)


def test_lookup_survives_restart_duplicate_callbacks_and_one_shot(direct):  # noqa: F811
    path, token = direct
    svc = DurableQBWCDiscoveryService.from_path(path)
    assert (
        account_job(svc, token, "connector-company-a", "preview", enqueue=True)["state"] == "queued"
    )
    ticket, _ = authenticate(svc)
    request = send(svc, ticket, hcp=hcp_for())
    assert "AccountQueryRq" in request and 'version="13.0"' in request
    svc = DurableQBWCDiscoveryService.from_path(path)
    assert send(svc, ticket, hcp=hcp_for()) == request
    response = FakeQuickBooks(
        entities={"Host": [HOST], "Company": [COMPANY_A], "Account": [ACCOUNT]}
    )(request)
    assert receive(svc, ticket, response) == 100
    assert receive(svc, ticket, response) == 100
    assert call(svc, "closeConnection", ticket=ticket) == "OK"
    result = account_job(svc, token, "connector-company-a", "preview")
    assert result["accounts"] == [ACCOUNT] and result["complete"] is False
    ticket2, _ = authenticate(svc)
    assert "AccountQueryRq" not in send(svc, ticket2)


def test_missing_hcp_blocks_lookup(direct):  # noqa: F811
    path, token = direct
    svc = DurableQBWCDiscoveryService.from_path(path)
    account_job(svc, token, "connector-company-a", "preview", enqueue=True)
    ticket, _ = authenticate(svc)
    assert send(svc, ticket) == ""
    assert "accounts" not in account_job(svc, token, "connector-company-a", "preview")


def test_revoke_read_before_dispatch(direct):  # noqa: F811
    path, token = direct
    svc = DurableQBWCDiscoveryService.from_path(path)
    account_job(svc, token, "connector-company-a", "preview", enqueue=True)
    ticket, _ = authenticate(svc)
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = []
    path.write_text(json.dumps(raw))
    svc = DurableQBWCDiscoveryService.from_path(path)
    assert send(svc, ticket, hcp=hcp_for()) == ""
    with pytest.raises(BridgeError):
        account_job(svc, token, "connector-company-a", "preview")


def test_wrong_company_never_releases_accounts(direct):  # noqa: F811
    path, token = direct
    svc = DurableQBWCDiscoveryService.from_path(path)
    account_job(svc, token, "connector-company-a", "preview", enqueue=True)
    ticket, _ = authenticate(svc)
    request = send(svc, ticket, hcp=hcp_for())
    response = FakeQuickBooks(
        entities={"Host": [HOST], "Company": [COMPANY_B], "Account": [ACCOUNT]}
    )(request)
    assert receive(svc, ticket, response) == -1
    assert "accounts" not in account_job(svc, token, "connector-company-a", "preview")
