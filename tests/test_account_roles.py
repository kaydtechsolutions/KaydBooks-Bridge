"""Role policy checks use synthetic, persisted exact lookup evidence."""

import json

import pytest

from kaydbooks_bridge.account_roles import check_role
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.qbwc_accounts import account_job
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import ACCOUNT, account_transport, direct  # noqa: F401
from test_qbwc_discovery import (  # noqa: F401
    COMPANY_A,
    HOST,
    PASSWORD_A,
    authenticate,
    discovery_setup,
    hcp_for,
    receive,
    send,
)


@pytest.fixture
def policy(direct):  # noqa: F811
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"].append("validate")
    raw["companies"]["company-a"]["account_roles"] = {"invoice_receivable": ACCOUNT["ListID"]}
    path.write_text(json.dumps(raw))
    return path, token


def evidence(policy, transport, account_type="AccountsReceivable", preview=False):
    path, token = policy
    svc = DurableQBWCDiscoveryService.from_path(path)
    record = {**ACCOUNT, "AccountType": account_type}
    if transport == "direct-sdk":
        discover(
            svc,
            token,
            "connector-company-a",
            PASSWORD_A,
            "919",
            exchange=account_transport([record]),
            accounts=preview,
            list_id=None if preview else ACCOUNT["ListID"],
        )
    else:
        account_job(
            svc,
            token,
            "connector-company-a",
            "role-evidence",
            enqueue=True,
            list_id=None if preview else ACCOUNT["ListID"],
        )
        ticket, _ = authenticate(svc)
        request = send(svc, ticket, hcp=hcp_for())
        assert (
            receive(
                svc,
                ticket,
                FakeQuickBooks(
                    entities={"Host": [HOST], "Company": [COMPANY_A], "Account": [record]}
                )(request),
            )
            == 100
        )
    return svc


def check(svc, token, transport):
    return check_role(
        svc,
        token,
        "connector-company-a",
        "invoice.create",
        "receivable",
        transport,
        "919" if transport == "direct-sdk" else "role-evidence",
    )


@pytest.mark.parametrize("transport", ["direct-sdk", "qbwc"])
def test_verified_exact_role_and_audit(policy, transport):
    evidence(policy, transport)
    svc = DurableQBWCDiscoveryService.from_path(policy[0])
    result = check(svc, policy[1], transport)
    assert result["state"] == "role-matched" and result["scope"] == "saved-evidence-only"
    assert not result["live_posting"] and "accounts" not in result
    with svc._stores["company-a"].transaction() as db:
        assert svc._stores["company-a"].verify_audit(db)


@pytest.mark.parametrize("transport", ["direct-sdk", "qbwc"])
@pytest.mark.parametrize(
    "case", ["type", "preview", "mapping", "permission", "binding", "unconfigured"]
)
def test_role_rejects_incompatible_or_changed_context(policy, transport, case):
    evidence(
        policy,
        transport,
        account_type="Income" if case == "type" else "AccountsReceivable",
        preview=case == "preview",
    )
    path, token = policy
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    if case == "mapping":
        raw["companies"]["company-a"]["account_roles"]["invoice_receivable"] = "another-id"
    elif case == "permission":
        raw["principals"][actor]["companies"]["company-a"].remove("validate")
    elif case == "binding":
        raw["connectors"]["connector-company-a"]["identity_sha256"] = "f" * 64
    elif case == "unconfigured":
        raw["companies"]["company-a"].pop("account_roles")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        check(DurableQBWCDiscoveryService.from_path(path), token, transport)


@pytest.mark.parametrize(
    "roles",
    [None, [], {"unknown": "id"}, {"invoice_receivable": None}, {"invoice_receivable": "<bad/>"}],
)
def test_bad_role_config_rejected(policy, roles):
    path, _ = policy
    raw = json.loads(path.read_text())
    raw["companies"]["company-a"]["account_roles"] = roles
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        Config.load(path)


@pytest.mark.parametrize("transport", ["direct-sdk", "qbwc"])
def test_evidence_cannot_be_reused_by_another_principal(policy, transport, monkeypatch):
    evidence(policy, transport)
    path, _ = policy
    raw = json.loads(path.read_text())
    raw["principals"]["another-validator"] = {
        "token_env": "KAYDBOOKS_OTHER_VALIDATOR",
        "companies": {"company-a": ["read", "validate"]},
    }
    path.write_text(json.dumps(raw))
    token = "synthetic-other-validator-" + "z" * 32
    monkeypatch.setenv("KAYDBOOKS_OTHER_VALIDATOR", token)
    with pytest.raises(BridgeError, match="ownership"):
        check(DurableQBWCDiscoveryService.from_path(path), token, transport)


@pytest.mark.parametrize(
    "operation,role,transport",
    [
        ("bill.create", "receivable", "direct-sdk"),
        ("invoice.create", "income", "direct-sdk"),
        ("invoice.create", "receivable", "untrusted"),
    ],
)
def test_unsupported_rule_or_transport(policy, operation, role, transport):
    svc = DurableQBWCDiscoveryService.from_path(policy[0])
    with pytest.raises(BridgeError, match="unsupported"):
        check_role(svc, policy[1], "connector-company-a", operation, role, transport, "919")
