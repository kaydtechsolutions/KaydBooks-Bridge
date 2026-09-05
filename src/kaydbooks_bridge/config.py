"""Private configuration is operator-controlled, never supplied by document content."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


class BridgeError(ValueError):
    """A safe, non-sensitive error suitable for a client response."""


def strict_keys(value, required: set[str], optional: set[str] | None = None):
    if not isinstance(value, dict) or not required <= value.keys():
        raise BridgeError("missing required fields")
    if value.keys() - required - (optional or set()):
        raise BridgeError("unsupported fields")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise BridgeError("invalid identifier")
    return value


def outside_repository(path: Path) -> Path:
    path = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise BridgeError("private configuration and state must be outside any Git checkout")
    return path


@dataclass(frozen=True)
class Company:
    id: str
    simulation_identity: str
    currency: str
    max_total: str
    customers: tuple[str, ...]
    items: tuple[str, ...]
    sources: tuple[str, ...]
    approval_required: bool


@dataclass(frozen=True)
class Config:
    root: Path
    companies: dict[str, Company]
    principals: dict[str, dict]

    @classmethod
    def load(cls, path: str | Path) -> Config:
        source = outside_repository(Path(path))
        data = json.loads(source.read_text(encoding="utf-8"))
        strict_keys(data, {"schema_version", "mode", "state_root", "companies", "principals"})
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise BridgeError("unsupported configuration version")
        if data["mode"] != "simulation":
            raise BridgeError("live posting is disabled in this build")
        root = Path(data["state_root"]).expanduser()
        if not root.is_absolute():
            raise BridgeError("state_root must be absolute")
        root = outside_repository(root)
        if not isinstance(data["companies"], dict) or not data["companies"]:
            raise BridgeError("at least one explicit company is required")
        companies = {}
        for name, raw in data["companies"].items():
            identifier(name)
            strict_keys(
                raw,
                {
                    "simulation_identity",
                    "currency",
                    "max_total",
                    "customers",
                    "items",
                    "sources",
                    "approval_required",
                },
            )
            identifier(raw["simulation_identity"])
            if not isinstance(raw["currency"], str) or not re.fullmatch(
                r"[A-Z]{3}", raw["currency"]
            ):
                raise BridgeError("invalid base currency")
            from .validation import money

            money(raw["max_total"])
            for key in ("customers", "items", "sources"):
                if not isinstance(raw[key], list) or not raw[key]:
                    raise BridgeError("nonempty company allowlists required")
                for entry in raw[key]:
                    identifier(entry)
                raw[key] = tuple(raw[key])
            if type(raw["approval_required"]) is not bool:
                raise BridgeError("approval_required must be boolean")
            companies[name] = Company(id=name, **raw)
        principals = data["principals"]
        if not isinstance(principals, dict) or not principals:
            raise BridgeError("principals required")
        env_names = set()
        for name, principal in principals.items():
            identifier(name)
            strict_keys(principal, {"token_env", "companies"})
            env = principal["token_env"]
            if not isinstance(env, str) or not re.fullmatch(r"KAYDBOOKS_[A-Z0-9_]+", env):
                raise BridgeError("use a KAYDBOOKS_ environment secret reference")
            if env in env_names:
                raise BridgeError("principal secret references must be unique")
            env_names.add(env)
            grants = principal["companies"]
            if not isinstance(grants, dict):
                raise BridgeError("invalid permission grants")
            for company, permissions in grants.items():
                if company not in companies or not isinstance(permissions, list):
                    raise BridgeError("invalid company permission grants")
                if any(p not in PERMISSIONS for p in permissions):
                    raise BridgeError("unsupported permission")
        return cls(root, companies, principals)

    def authenticate(self, token: str) -> str:
        matches = []
        for name, principal in self.principals.items():
            expected = os.environ.get(principal["token_env"], "")
            if len(expected) >= 32 and secrets.compare_digest(token.encode(), expected.encode()):
                matches.append(name)
        if len(matches) != 1:
            raise BridgeError("authentication failed")
        return matches[0]

    def authorize(self, actor: str, company: str, permission: str) -> Company:
        principal = self.principals.get(actor, {})
        if company not in self.companies or permission not in principal.get("companies", {}).get(
            company, []
        ):
            raise BridgeError("permission denied")
        return self.companies[company]


PERMISSIONS = frozenset(
    {"prepare", "read", "validate", "approve", "submit", "simulate", "recover", "pause"}
)
