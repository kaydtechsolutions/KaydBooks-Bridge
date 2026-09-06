"""Private configuration is operator-controlled, never supplied by document content."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
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
    account_roles: dict[str, str] = field(default_factory=dict)
    invoice_masters: dict = field(default_factory=dict)
    invoice_evidence_max_age_seconds: int = 900


@dataclass(frozen=True)
class Connector:
    id: str
    company: str
    password_env: str
    company_file_env: str | None
    identity_fields: tuple[str, ...]
    identity_sha256: str


@dataclass(frozen=True)
class Config:
    root: Path
    companies: dict[str, Company]
    principals: dict[str, dict]
    connectors: dict[str, Connector]

    @classmethod
    def load(cls, path: str | Path) -> Config:
        source = outside_repository(Path(path))
        data = json.loads(source.read_text(encoding="utf-8"))
        strict_keys(
            data,
            {"schema_version", "mode", "state_root", "companies", "principals"},
            {"connectors"},
        )
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
                {"account_roles", "invoice_masters", "invoice_evidence_max_age_seconds"},
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
            from .account_roles import validate_roles

            raw["account_roles"] = validate_roles(raw.get("account_roles", {}))
            from .invoice_compatibility import validate_masters

            raw["invoice_masters"] = validate_masters(
                raw.get("invoice_masters", {}), raw["customers"], raw["items"]
            )
            age = raw.get("invoice_evidence_max_age_seconds", 900)
            if type(age) is not int or not 60 <= age <= 86400:
                raise BridgeError("invoice evidence age must be 60-86400 seconds")
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
        connectors = {}
        identity_companies: dict[str, str] = {}
        company_identities: dict[str, tuple[tuple[str, ...], str]] = {}
        for name, raw in data.get("connectors", {}).items():
            identifier(name)
            strict_keys(
                raw,
                {"company", "password_env", "identity_fields", "identity_sha256"},
                {"company_file_env"},
            )
            company = raw["company"]
            if company not in companies:
                raise BridgeError("connector references an unknown company")
            password_env = _secret_env(raw["password_env"])
            if password_env in env_names:
                raise BridgeError("secret references must be unique")
            env_names.add(password_env)
            company_file_env = raw.get("company_file_env")
            if company_file_env is not None:
                company_file_env = _secret_env(company_file_env)
                if company_file_env in env_names:
                    raise BridgeError("secret references must be unique")
                env_names.add(company_file_env)
            fields = raw["identity_fields"]
            if (
                not isinstance(fields, list)
                or len(fields) < 3
                or len(fields) != len(set(fields))
                or any(field not in COMPANY_IDENTITY_FIELDS for field in fields)
                or not set(fields) & STRONG_COMPANY_IDENTITY_FIELDS
            ):
                raise BridgeError("company identity requires distinct supported claims")
            expected = raw["identity_sha256"]
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise BridgeError("company identity SHA-256 is invalid")
            other_company = identity_companies.get(expected)
            if other_company is not None and other_company != company:
                raise BridgeError("company identity binding is ambiguous")
            company_identity = (tuple(fields), expected)
            if company in company_identities and company_identities[company] != company_identity:
                raise BridgeError("configured company has inconsistent identity bindings")
            identity_companies[expected] = company
            company_identities[company] = company_identity
            connectors[name] = Connector(
                id=name,
                company=company,
                password_env=password_env,
                company_file_env=company_file_env,
                identity_fields=tuple(fields),
                identity_sha256=expected,
            )
        return cls(root, companies, principals, connectors)

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

    def authenticate_connector(self, username: str, password: str) -> Connector:
        connector = self.connectors.get(username)
        if connector is None:
            raise BridgeError("authentication failed")
        expected = os.environ.get(connector.password_env, "")
        if len(expected) < 32 or not secrets.compare_digest(password.encode(), expected.encode()):
            raise BridgeError("authentication failed")
        return connector

    @staticmethod
    def connector_company_file(connector: Connector) -> str:
        if connector.company_file_env is None:
            return ""
        value = os.environ.get(connector.company_file_env, "")
        if not value or len(value) > 1024 or any(char in value for char in "\r\n\0"):
            raise BridgeError("configured company file is unavailable")
        return value


def _secret_env(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"KAYDBOOKS_[A-Z0-9_]+", value):
        raise BridgeError("use a KAYDBOOKS_ environment secret reference")
    return value


COMPANY_IDENTITY_FIELDS = frozenset(
    {
        "CompanyName",
        "LegalCompanyName",
        "EIN",
        "SSN",
        "Phone",
        "Email",
        "TaxForm",
        "FirstMonthFiscalYear",
        "FirstMonthIncomeTaxYear",
        "Address.Addr1",
        "Address.City",
        "Address.State",
        "Address.PostalCode",
        "LegalAddress.Addr1",
        "LegalAddress.City",
        "LegalAddress.State",
        "LegalAddress.PostalCode",
    }
)
STRONG_COMPANY_IDENTITY_FIELDS = COMPANY_IDENTITY_FIELDS - {
    "CompanyName",
    "LegalCompanyName",
    "FirstMonthFiscalYear",
    "FirstMonthIncomeTaxYear",
}


PERMISSIONS = frozenset(
    {"prepare", "read", "validate", "approve", "submit", "simulate", "recover", "pause"}
)
