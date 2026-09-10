"""Production authority is explicit; legacy roles, overrides and stale actors cannot grant it."""

import json
import os

import pytest

from kaydbooks_bridge import access
from kaydbooks_bridge.config import (
    ALL_PERMISSIONS,
    PERMISSIONS,
    PRODUCTION_PERMISSIONS,
    BridgeError,
    Config,
)
from kaydbooks_bridge.service import Bridge
from test_bridge import TOKENS, ledger_for, queue, setup  # noqa: F401


@pytest.fixture
def production_access(setup):  # noqa: F811
    bridge, path, raw, envelope = setup
    raw["principals"]["operator-a"]["companies"]["company-a"] = sorted(PERMISSIONS)
    path.write_text(json.dumps(raw))
    return bridge, path, raw, envelope


def reviewed_revision(case):
    return access.inspect(case[0], TOKENS["operator-a"], "company-a")["config_revision"]


def change(case, *, principal="preparer-a", permissions=None, **kwargs):
    return access.set_user(
        case[0],
        TOKENS["operator-a"],
        "company-a",
        principal,
        reviewed_revision(case),
        permissions=permissions,
        **kwargs,
    )


def allow_production_admin(case):
    _, path, raw, _ = case
    raw["principals"]["operator-a"]["companies"]["company-a"].append("manage-production")
    path.write_text(json.dumps(raw))


def test_legacy_roles_and_defaults_never_expand():
    assert not access.ROLES["administrator"] & PRODUCTION_PERMISSIONS
    assert not set(access.permissions_for(roles=list(access.ROLES))) & PRODUCTION_PERMISSIONS
    assert access.permissions_for() == ["read"]
    assert set(access.permissions_for(permissions=sorted(ALL_PERMISSIONS))) == ALL_PERMISSIONS


@pytest.mark.parametrize("permission", sorted(PRODUCTION_PERMISSIONS))
def test_legacy_admin_cannot_grant_production_to_self_or_others(production_access, permission):
    _, path, _, _ = production_access
    before = path.read_bytes()
    for principal in ("operator-a", "preparer-a"):
        with pytest.raises(BridgeError, match="permission denied"):
            change(production_access, principal=principal, permissions=["read", permission])
        assert path.read_bytes() == before


def test_designated_owner_cannot_bypass_production_grants(production_access):
    _, path, raw, _ = production_access
    raw["principals"]["operator-a"]["owner"] = True
    raw["owner_access"] = {
        "company-a": {
            "principal": "operator-a",
            "activated_at": 1000,
            "expires_at": 2000,
            "reason": "Synthetic maintenance test",
        }
    }
    path.write_text(json.dumps(raw))
    bridge = Bridge(path, clock=lambda: 1500)
    config = Config.load(path)
    assert config.owner_override("operator-a", "company-a", 1500)
    for permission in (*PRODUCTION_PERMISSIONS, "arbitrary-permission"):
        with pytest.raises(BridgeError, match="permission denied"):
            config.authorize("operator-a", "company-a", permission, at=1500)
    with pytest.raises(BridgeError, match="permission denied"):
        access.set_user(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "operator-a",
            access.inspect(bridge, TOKENS["operator-a"], "company-a")["config_revision"],
            permissions=sorted(ALL_PERMISSIONS),
        )


def test_explicit_grant_is_scoped_audited_and_does_not_enable_posting(production_access):
    bridge, path, _, _ = production_access
    allow_production_admin(production_access)
    result = change(production_access, permissions=["read", "post-production"])
    config = Config.load(path)
    config.authorize("preparer-a", "company-a", "post-production")
    for company, permission in (
        ("company-b", "post-production"),
        ("company-a", "manage-production"),
    ):
        with pytest.raises(BridgeError, match="permission denied"):
            config.authorize("preparer-a", company, permission)
    assert result["permissions"] == ["post-production", "read"]
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]
    assert json.loads(path.read_text())["mode"] == "simulation"
    changed = json.loads(path.read_text())
    changed["mode"] = "production"
    path.write_text(json.dumps(changed))
    with pytest.raises(BridgeError, match="live posting is disabled"):
        Config.load(path)


def test_production_revocation_requires_explicit_admin_and_is_immediate(production_access):
    _, path, _, _ = production_access
    allow_production_admin(production_access)
    change(production_access, permissions=["read", "post-production"])
    raw = json.loads(path.read_text())
    raw["principals"]["operator-a"]["companies"]["company-a"].remove("manage-production")
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="permission denied"):
        change(production_access, permissions=["read"])
    raw["principals"]["operator-a"]["companies"]["company-a"].append("manage-production")
    path.write_text(json.dumps(raw))
    change(production_access, permissions=["read"])
    with pytest.raises(BridgeError, match="permission denied"):
        Config.load(path).authorize("preparer-a", "company-a", "post-production")


def test_disabled_principal_cannot_authenticate_or_use_saved_actor(production_access):
    bridge, path, raw, _ = production_access
    raw["principals"]["preparer-a"]["disabled"] = True
    path.write_text(json.dumps(raw))
    config = Config.load(path)
    with pytest.raises(BridgeError, match="authentication failed"):
        config.authenticate(TOKENS["preparer-a"])
    with pytest.raises(BridgeError, match="permission denied"):
        config.authorize("preparer-a", "company-a", "read")
    with pytest.raises(BridgeError, match="authentication failed"):
        bridge._context(TOKENS["preparer-a"], "company-a", "read")


@pytest.mark.parametrize("bad", [None, 0, 1, "false", [], {}])
def test_disabled_requires_a_real_boolean(production_access, bad):
    _, path, raw, _ = production_access
    raw["principals"]["preparer-a"]["disabled"] = bad
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="disabled state must be boolean"):
        Config.load(path)


@pytest.mark.parametrize("grants", [["read", "read"], [["read"]], [None]])
def test_invalid_grants_fail_with_safe_error(production_access, grants):
    _, path, raw, _ = production_access
    raw["principals"]["preparer-a"]["companies"]["company-a"] = grants
    path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        Config.load(path)


def test_rotation_revokes_old_token_and_preserves_grants(production_access, monkeypatch):
    bridge, path, raw, _ = production_access
    new = "new-synthetic-secret-" + "x" * 40
    monkeypatch.setenv("KAYDBOOKS_ROTATED", new)
    result = access.rotate_credential(
        bridge,
        TOKENS["operator-a"],
        "company-a",
        "preparer-a",
        reviewed_revision(production_access),
        "KAYDBOOKS_ROTATED",
    )
    config = Config.load(path)
    assert config.authenticate(new) == "preparer-a"
    with pytest.raises(BridgeError, match="authentication failed"):
        config.authenticate(TOKENS["preparer-a"])
    assert (
        config.principals["preparer-a"]["companies"] == raw["principals"]["preparer-a"]["companies"]
    )
    assert new not in path.read_text() and new not in json.dumps(result)
    audit = bridge.audit(TOKENS["operator-a"], "company-a")
    assert audit["valid"] and new not in json.dumps(audit)


@pytest.mark.parametrize("value", ["short", None, TOKENS["operator-a"], TOKENS["preparer-a"]])
def test_rotation_requires_provisioned_distinct_secret(production_access, monkeypatch, value):
    bridge, path, _, _ = production_access
    if value is not None:
        monkeypatch.setenv("KAYDBOOKS_ROTATED", value)
    else:
        monkeypatch.delenv("KAYDBOOKS_ROTATED", raising=False)
    before = path.read_bytes()
    with pytest.raises(BridgeError, match="new distinct credential"):
        access.rotate_credential(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "preparer-a",
            reviewed_revision(production_access),
            "KAYDBOOKS_ROTATED",
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["rotate", "disable"])
def test_global_principal_changes_require_all_company_authority(
    production_access, monkeypatch, action
):
    bridge, path, raw, _ = production_access
    raw["principals"]["preparer-a"]["companies"]["company-b"] = ["read"]
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("KAYDBOOKS_ROTATED", "rotated-" + "z" * 40)
    before = path.read_bytes()
    rev = reviewed_revision(production_access)
    with pytest.raises(BridgeError, match="permission denied"):
        if action == "rotate":
            access.rotate_credential(
                bridge, TOKENS["operator-a"], "company-a", "preparer-a", rev, "KAYDBOOKS_ROTATED"
            )
        else:
            access.set_disabled(bridge, TOKENS["operator-a"], "company-a", "preparer-a", rev, True)
    assert path.read_bytes() == before


def test_production_identity_cannot_be_rotated_by_legacy_admin(production_access, monkeypatch):
    bridge, path, raw, _ = production_access
    raw["principals"]["preparer-a"]["companies"]["company-a"].append("post-production")
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("KAYDBOOKS_ROTATED", "rotated-" + "z" * 40)
    with pytest.raises(BridgeError, match="permission denied"):
        access.rotate_credential(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "preparer-a",
            reviewed_revision(production_access),
            "KAYDBOOKS_ROTATED",
        )


def test_disable_and_reenable_are_audited_without_changing_grants(production_access):
    bridge, path, raw, _ = production_access
    for disabled in (True, False):
        result = access.set_disabled(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            "preparer-a",
            reviewed_revision(production_access),
            disabled,
        )
        assert result["disabled"] is disabled
        config = Config.load(path)
        if disabled:
            with pytest.raises(BridgeError, match="authentication failed"):
                config.authenticate(TOKENS["preparer-a"])
        else:
            assert config.authenticate(TOKENS["preparer-a"]) == "preparer-a"
        assert (
            config.principals["preparer-a"]["companies"]
            == raw["principals"]["preparer-a"]["companies"]
        )
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX service ownership and file mode")
def test_atomic_access_change_preserves_service_file_permissions(production_access):
    _, path, _, _ = production_access
    path.chmod(0o640)
    before = path.stat()
    change(production_access, permissions=["read"])
    after = path.stat()
    assert after.st_mode & 0o777 == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


def test_disabling_approver_revokes_queued_approval_before_dispatch(production_access):
    bridge, _, _, _ = production_access
    job = queue(production_access)
    access.set_disabled(
        bridge,
        TOKENS["operator-a"],
        "company-a",
        "approver-a",
        reviewed_revision(production_access),
        True,
    )
    with pytest.raises(BridgeError, match="permission denied"):
        bridge.simulate(TOKENS["operator-a"], "company-a")
    _, ledger = ledger_for(production_access)
    assert ledger.find(job["payload"]) == []
    assert bridge.status(TOKENS["operator-a"], "company-a", job["id"])["state"] != "verified"


def test_cli_rotation_and_disable_never_accept_or_echo_secret(
    production_access, monkeypatch, tmp_path, capsys
):
    _, path, _, _ = production_access
    new = "new-cli-" + "z" * 40
    monkeypatch.setenv("KAYDBOOKS_ROTATED", new)
    monkeypatch.setenv("KAYDBOOKS_TOKEN", TOKENS["operator-a"])
    request = tmp_path / "rotate.json"
    request.write_text(
        json.dumps(
            {
                "principal": "preparer-a",
                "expected_revision": reviewed_revision(production_access),
                "token_env": "KAYDBOOKS_ROTATED",
            }
        )
    )
    assert (
        access.main(
            ["--config", str(path), "--company", "company-a", "rotate-credential", str(request)]
        )
        == 0
    )
    assert Config.load(path).authenticate(new) == "preparer-a"
    request.write_text(
        json.dumps(
            {
                "principal": "preparer-a",
                "expected_revision": reviewed_revision(production_access),
                "disabled": True,
            }
        )
    )
    assert (
        access.main(["--config", str(path), "--company", "company-a", "disable-user", str(request)])
        == 0
    )
    with pytest.raises(BridgeError, match="authentication failed"):
        Config.load(path).authenticate(new)
    assert new not in capsys.readouterr().out


def test_disabled_owner_cannot_use_existing_override(production_access):
    _, path, raw, _ = production_access
    raw["principals"]["operator-a"].update(owner=True, disabled=True)
    raw["owner_access"] = {
        "company-a": {
            "principal": "operator-a",
            "activated_at": 1000,
            "expires_at": 2000,
            "reason": "Synthetic maintenance test",
        }
    }
    path.write_text(json.dumps(raw))
    config = Config.load(path)
    assert not config.owner_override("operator-a", "company-a", 1500)
    with pytest.raises(BridgeError, match="permission denied"):
        config.authorize("operator-a", "company-a", "approve", at=1500)
