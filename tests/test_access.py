"""Company access changes must be explicit, scoped, current and effective on queued work."""

# ruff: noqa: F811
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from kaydbooks_bridge import access
from kaydbooks_bridge.config import PERMISSIONS, BridgeError, Config
from kaydbooks_bridge.service import Bridge
from test_bridge import TOKENS, queue, setup  # noqa: F401


@pytest.fixture
def managed(setup):
    bridge, path, raw, envelope = setup
    raw["principals"]["operator-a"]["companies"]["company-a"] = sorted(PERMISSIONS)
    path.write_text(json.dumps(raw))
    return setup


def current(managed):
    return access.inspect(managed[0], TOKENS["operator-a"], "company-a")["config_revision"]


def test_new_user_full_default_is_company_scoped(managed, monkeypatch):
    bridge, path, _, _ = managed
    other = json.loads(path.read_text())["companies"]["company-b"]
    token = "synthetic-new-" + "n" * 32
    monkeypatch.setenv("KAYDBOOKS_NEW_USER", token)
    result = access.set_user(
        bridge,
        TOKENS["operator-a"],
        "company-a",
        "new-user",
        current(managed),
        token_env="KAYDBOOKS_NEW_USER",
    )
    config = Config.load(path)
    actor = config.authenticate(token)
    assert set(result["permissions"]) == PERMISSIONS
    for permission in PERMISSIONS:
        config.authorize(actor, "company-a", permission)
        with pytest.raises(BridgeError):
            config.authorize(actor, "company-b", permission)
    assert json.loads(path.read_text())["companies"]["company-b"] == other
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]
    assert token not in json.dumps(result)


@pytest.mark.parametrize(
    "roles,expected",
    [
        ([], set()),
        (["preparer", "approver"], access.ROLES["preparer"] | access.ROLES["approver"]),
        (["preparer", "approver", "administrator"], PERMISSIONS),
    ],
)
def test_combined_roles_and_individual_restrictions(roles, expected):
    assert set(access.permissions_for(roles=roles)) == expected
    assert set(access.permissions_for(roles=roles, deny=["submit"])) == expected - {"submit"}
    assert access.permissions_for(permissions=[]) == []
    assert access.permissions_for(permissions=["prepare", "read"], deny=["prepare"]) == ["read"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"roles": ["root"]},
        {"permissions": ["post-production"]},
        {"roles": ["preparer", "preparer"]},
        {"deny": ["shell"]},
        {"roles": [], "permissions": []},
    ],
)
def test_invalid_role_requests_rejected(arguments):
    with pytest.raises(BridgeError):
        access.permissions_for(**arguments)


def test_current_revision_prevents_lost_updates(managed):
    bridge, path, _, _ = managed
    rev = current(managed)
    access.set_user(
        bridge, TOKENS["operator-a"], "company-a", "preparer-a", rev, roles=["preparer", "approver"]
    )
    saved = path.read_bytes()
    with pytest.raises(BridgeError, match="configuration changed"):
        access.set_user(
            bridge, TOKENS["operator-a"], "company-a", "approver-a", rev, permissions=[]
        )
    assert path.read_bytes() == saved


def test_cross_company_admin_and_credential_replacement_denied(managed):
    bridge, path, _, _ = managed
    saved = path.read_bytes()
    for actor, company in (("preparer-a", "company-a"), ("operator-a", "company-b")):
        with pytest.raises(BridgeError):
            access.set_user(
                bridge,
                TOKENS[actor],
                company,
                "preparer-a",
                current(managed),
                roles=["administrator"],
            )
    with pytest.raises(BridgeError, match="cannot replace"):
        access.set_user(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "preparer-a",
            current(managed),
            token_env="KAYDBOOKS_REPLACEMENT",
        )
    assert path.read_bytes() == saved


def test_approval_policy_is_explicit_and_rechecked(managed):
    bridge, path, _, envelope = managed
    access.set_user(
        bridge,
        TOKENS["operator-a"],
        "company-a",
        "preparer-a",
        current(managed),
        roles=["preparer", "approver"],
    )
    job = bridge.prepare(TOKENS["preparer-a"], "company-a", envelope)
    bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "validate")
    with pytest.raises(BridgeError, match="different principal"):
        bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "approve")
    access.set_self_approval(bridge, TOKENS["operator-a"], "company-a", current(managed), True)
    bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "approve")
    access.set_self_approval(bridge, TOKENS["operator-a"], "company-a", current(managed), False)
    with pytest.raises(BridgeError, match="self-approval policy"):
        bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "submit")
    assert Bridge(path).status(TOKENS["preparer-a"], "company-a", job["id"])["state"] == "validated"


def test_queued_job_respects_revoked_submit_permission(managed):
    bridge, path, _, _ = managed
    job = queue(managed)
    access.set_user(
        bridge,
        TOKENS["operator-a"],
        "company-a",
        "preparer-a",
        current(managed),
        roles=["preparer"],
        deny=["submit"],
    )
    with pytest.raises(BridgeError):
        bridge.simulate(TOKENS["operator-a"], "company-a")
    assert Bridge(path).status(TOKENS["operator-a"], "company-a", job["id"])["state"] == "queued"


def test_invalid_candidate_or_atomic_replace_failure_preserves_config(managed, monkeypatch):
    bridge, path, _, _ = managed
    saved = path.read_bytes()
    rev = current(managed)
    with pytest.raises(BridgeError):
        access.set_user(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "new-user",
            rev,
            token_env="KAYDBOOKS_OPERATOR_A_SECRET",
        )
    assert path.read_bytes() == saved

    def fail(*args):
        raise OSError("test interrupted replacement")

    monkeypatch.setattr(access.os, "replace", fail)
    with pytest.raises(OSError):
        access.set_user(
            bridge, TOKENS["operator-a"], "company-a", "preparer-a", rev, permissions=[]
        )
    assert path.read_bytes() == saved and not list(path.parent.glob("*.pending"))
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]


def test_concurrent_access_changes_do_not_overwrite_each_other(managed):
    bridge, path, _, _ = managed
    rev = current(managed)

    def update(principal):
        try:
            return access.set_user(
                bridge, TOKENS["operator-a"], "company-a", principal, rev, permissions=[]
            )
        except BridgeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["preparer-a", "approver-a"]))
    assert sum(r is not None for r in results) == 1
    grants = Config.load(path).principals
    assert sum(grants[p]["companies"]["company-a"] == [] for p in ("preparer-a", "approver-a")) == 1


def test_cli_and_hermes_use_same_access_contract(managed, monkeypatch, capsys, tmp_path):
    from kaydbooks_bridge.hermes_tools import Tools

    bridge, path, _, _ = managed
    monkeypatch.setenv("KAYDBOOKS_TOKEN", TOKENS["operator-a"])
    assert access.main(["--config", str(path), "--company", "company-a", "inspect"]) == 0
    rev = json.loads(capsys.readouterr().out)["config_revision"]
    request = tmp_path / "access-request.json"
    request.write_text(
        json.dumps(
            {"principal": "preparer-a", "expected_revision": rev, "roles": ["preparer", "approver"]}
        )
    )
    assert (
        access.main(["--config", str(path), "--company", "company-a", "set-user", str(request)])
        == 0
    )
    result = Tools(path, TOKENS["operator-a"]).call(
        "company_access_v1", "company-a", {"action": "inspect", "parameters": {}}
    )
    assert "approve" in result["users"]["preparer-a"]
    with pytest.raises(BridgeError):
        Tools(path, TOKENS["preparer-a"]).call(
            "company_access_v1", "company-a", {"action": "inspect", "parameters": {}}
        )
