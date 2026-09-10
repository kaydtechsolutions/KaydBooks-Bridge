"""Private company user administration with concrete grants and audited atomic updates."""

import argparse
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .config import PERMISSIONS, BridgeError, Config, identifier, outside_repository, strict_keys
from .direct_sdk import company_lock
from .service import Bridge, audited
from .validation import canonical

ROLES = {
    "preparer": frozenset({"read", "prepare", "validate", "submit", "review-source"}),
    "approver": frozenset({"read", "validate", "approve"}),
    "administrator": PERMISSIONS,
}


def permissions_for(*, roles=None, permissions=None, deny=None):
    if roles is not None and permissions is not None:
        raise BridgeError("choose role presets or an explicit permission list")

    def distinct(values, supported):
        if (
            not isinstance(values, list)
            or any(not isinstance(v, str) or v not in supported for v in values)
            or len(set(values)) != len(values)
        ):
            raise BridgeError("distinct supported roles and permissions required")
        return set(values)

    if permissions is not None:
        grants = distinct(permissions, PERMISSIONS)
    elif roles is not None:
        selected = distinct(roles, ROLES)
        grants = set().union(*(ROLES[r] for r in selected))
    else:
        grants = {"read"}
    return sorted(grants - distinct([] if deny is None else deny, PERMISSIONS))


def revision(content):
    return hashlib.sha256(content).hexdigest()


@audited
def inspect(bridge, token, company):
    path = outside_repository(Path(bridge.config_path))
    with company_lock(path.with_suffix(path.suffix + ".access.lock")):
        config, actor, policy, _ = bridge._context(token, company, "manage-users")
        return {
            "company": company,
            "config_revision": revision(Path(bridge.config_path).read_bytes()),
            "users": {
                name: sorted(principal["companies"][company])
                for name, principal in config.principals.items()
                if company in principal["companies"]
            },
            "role_presets": {name: sorted(values) for name, values in ROLES.items()},
            "allow_self_approval": policy.allow_self_approval,
            "new_user_default": "read-only-company-permissions",
            "owner_access": {
                "active": config.owner_override(actor, company, bridge.clock()),
                "expires_at": config.owner_access.get(company, {}).get("expires_at"),
                "principal": config.owner_access.get(company, {}).get("principal"),
            },
        }


def _change(bridge, token, company, expected_revision, mutate, description):
    path = outside_repository(Path(bridge.config_path))
    if not isinstance(expected_revision, str) or not re.fullmatch(
        r"[a-f0-9]{64}", expected_revision
    ):
        raise BridgeError("reviewed configuration revision required")
    with company_lock(path.with_suffix(path.suffix + ".access.lock")):
        _, actor, _, store = bridge._context(token, company, "manage-users")
        old = path.read_bytes()
        if revision(old) != expected_revision:
            raise BridgeError("configuration changed; review current access before updating")
        data = json.loads(old)
        previous = mutate(data)
        description = {**description, **previous}
        value = (canonical(data) + "\n").encode()
        change = uuid.uuid4().hex
        temporary = path.with_name(path.name + ".access-" + change + ".pending")
        try:
            with temporary.open("xb") as f:
                os.chmod(temporary, 0o600)
                f.write(value)
                f.flush()
                os.fsync(f.fileno())
            candidate = Config.load(temporary)
            if candidate.root != Config.load(path).root:
                raise BridgeError("access update cannot move company state")
            with store.transaction() as db:
                if not store.verify_audit(db):
                    raise BridgeError("invalid access audit")
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    None,
                    "access_change_prepared",
                    {
                        "change": change,
                        "before": expected_revision,
                        "after": revision(value),
                        **description,
                    },
                )
            if path.read_bytes() != old:
                raise BridgeError("configuration changed during access update")
            os.replace(temporary, path)
            with store.transaction() as db:
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    None,
                    "access_change_applied",
                    {"change": change, "config_revision": revision(value)},
                )
        finally:
            if temporary.exists():
                temporary.unlink()
    return {"company": company, "updated": True, "config_revision": revision(value), **description}


@audited
def set_user(
    bridge,
    token,
    company,
    principal,
    expected_revision,
    *,
    roles=None,
    permissions=None,
    deny=None,
    token_env=None,
):
    identifier(principal)
    grants = permissions_for(roles=roles, permissions=permissions, deny=deny)

    def mutate(data):
        users = data["principals"]
        if principal in users:
            if token_env is not None and token_env != users[principal]["token_env"]:
                raise BridgeError(
                    "company administration cannot replace an existing user credential"
                )
        else:
            if not isinstance(token_env, str) or not re.fullmatch(
                r"KAYDBOOKS_[A-Z0-9_]+", token_env
            ):
                raise BridgeError("new user requires a private environment credential reference")
            users[principal] = {"token_env": token_env, "companies": {}}
        previous = users[principal]["companies"].get(company)
        users[principal]["companies"][company] = grants
        return {"previous_permissions": previous}

    return _change(
        bridge,
        token,
        company,
        expected_revision,
        mutate,
        {"principal": principal, "permissions": grants},
    )


@audited
def set_self_approval(bridge, token, company, expected_revision, allow):
    if type(allow) is not bool:
        raise BridgeError("self-approval policy must be boolean")

    def mutate(data):
        previous = data["companies"][company].get("allow_self_approval", False)
        data["companies"][company]["allow_self_approval"] = allow
        return {"previous_allow_self_approval": previous}

    return _change(
        bridge, token, company, expected_revision, mutate, {"allow_self_approval": allow}
    )


@audited
def set_owner_access(bridge, token, company, expected_revision, *, enabled, reason=None, hours=1):
    if type(enabled) is not bool:
        raise BridgeError("owner access state must be boolean")
    config = Config.load(bridge.config_path)
    actor = config.authenticate(token)
    if not config.principals.get(actor, {}).get("owner", False):
        raise BridgeError("designated owner authentication required")
    config.authorize(actor, company, "manage-users", at=bridge.clock())
    if enabled:
        if (
            not isinstance(reason, str)
            or not 10 <= len(reason) <= 256
            or type(hours) not in (int, float)
            or not 0 < hours <= 8
        ):
            raise BridgeError("owner access requires a reason and a window up to eight hours")
    elif reason is not None:
        raise BridgeError("disable does not accept a reason")

    def mutate(data):
        grants = data.setdefault("owner_access", {})
        previous = grants.get(company)
        expires = None
        if enabled:
            activated = bridge.clock()
            expires = activated + hours * 60 * 60
            grants[company] = {
                "principal": actor,
                "activated_at": activated,
                "expires_at": expires,
                "reason": reason,
            }
        else:
            grants.pop(company, None)
        return {
            "previous_owner_access": previous,
            "owner_access_expires_at": expires,
        }

    return _change(
        bridge,
        token,
        company,
        expected_revision,
        mutate,
        {
            "owner_access_enabled": enabled,
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Company users, combined roles and self-approval policy"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("action", choices=["inspect", "set-user", "self-approval", "owner-access"])
    parser.add_argument("input", nargs="?", type=Path)
    args = parser.parse_args(argv)
    try:
        bridge = Bridge(args.config)
        token = os.environ.get("KAYDBOOKS_TOKEN", "")
        if args.action == "inspect":
            if args.input is not None:
                raise BridgeError("inspect takes no input")
            result = inspect(bridge, token, args.company)
        else:
            if args.input is None or args.input.stat().st_size > 65536:
                raise BridgeError("bounded access request file required")
            value = json.loads(args.input.read_text(encoding="utf-8"))
            if args.action == "set-user":
                strict_keys(
                    value,
                    {"principal", "expected_revision"},
                    {"roles", "permissions", "deny", "token_env"},
                )
                result = set_user(bridge, token, args.company, **value)
            elif args.action == "self-approval":
                strict_keys(value, {"expected_revision", "allow"})
                result = set_self_approval(bridge, token, args.company, **value)
            else:
                strict_keys(
                    value,
                    {"expected_revision", "enabled"},
                    {"reason", "hours"},
                )
                result = set_owner_access(bridge, token, args.company, **value)
        print(canonical(result))
        return 0
    except (BridgeError, OSError, ValueError):
        print(
            canonical(
                {
                    "error": "access request failed; review permissions, input and current configuration revision"
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
