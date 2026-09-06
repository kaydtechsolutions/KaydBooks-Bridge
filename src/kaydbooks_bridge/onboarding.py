"""Private company setup and offline diagnostics; never opens QuickBooks or posts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from .config import BridgeError, Config, identifier, outside_repository, strict_keys


def private_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise BridgeError("absolute private path without symlinks required")
    return outside_repository(path)


def read_json(path: str | Path) -> dict:
    source = private_path(path)
    if not source.is_file() or source.stat().st_size > 131072:
        raise BridgeError("bounded private JSON file required")
    return json.loads(source.read_text(encoding="utf-8"))


def target_values(value: dict) -> dict:
    strict_keys(value, {"company_id", "company_name", "company_file"})
    identifier(value["company_id"])
    name = value["company_name"]
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 256
        or any(ord(c) < 32 for c in name)
    ):
        raise BridgeError("explicit company name required")
    path_value = value["company_file"]
    if not isinstance(path_value, str) or any(c in path_value for c in "\r\n\0"):
        raise BridgeError("invalid company file path")
    path = private_path(path_value)
    if path.suffix.lower() != ".qbw":
        raise BridgeError("company file must have .qbw extension")
    return {**value, "company_name": name.strip(), "company_file": str(path)}


def restrict_directory(path: Path):
    """Restrict a newly created directory before writing credentials."""
    if os.name == "nt":
        system = Path(os.environ["SYSTEMROOT"]) / "System32"
        identity = subprocess.run(
            [str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        sid = next(csv.reader(identity.stdout.strip().splitlines()))[1]
        if not sid.startswith("S-1-") or any(c not in "S-0123456789" for c in sid):
            raise BridgeError("unable to determine local operator identity")
        subprocess.run(
            [
                str(system / "icacls.exe"),
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
            ],
            capture_output=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        path.chmod(0o700)


def initialize(request_path: str | Path, destination: str | Path) -> dict:
    request = read_json(request_path)
    strict_keys(request, {"target", "currency", "max_total"})
    target = target_values(request["target"])
    root = private_path(destination)
    if root.exists() or not root.parent.is_dir():
        raise BridgeError("new destination under an existing private parent required")
    company = target["company_id"]
    config = {
        "schema_version": 1,
        "mode": "simulation",
        "state_root": str(root / "state"),
        "companies": {
            company: {
                "simulation_identity": company,
                "currency": request["currency"],
                "max_total": request["max_total"],
                "customers": ["configure-customer"],
                "items": ["configure-item"],
                "sources": ["documents"],
                "approval_required": True,
            }
        },
        "connectors": {
            "quickbooks": {
                "company": company,
                "password_env": "KAYDBOOKS_CONNECTOR_SECRET",
                "identity_fields": ["CompanyName", "LegalCompanyName", "EIN"],
                "identity_sha256": "0" * 64,
            }
        },
        "principals": {
            "operator": {
                "token_env": "KAYDBOOKS_OPERATOR_SECRET",
                "companies": {company: ["read"]},
            }
        },
    }
    # mkdir is exclusive, so concurrent initialization cannot overwrite a bundle.
    root.mkdir(mode=0o700)
    restrict_directory(root)
    # Invalid policy leaves a credential-free directory for inspection, never cleanup
    # that could remove an operator's existing files.
    config_path = root / "bridge-config.json"
    _write(config_path, config)
    Config.load(config_path)
    _write(root / "target.json", target)
    _write(
        root / "credentials.json",
        {
            "KAYDBOOKS_OPERATOR_SECRET": secrets.token_urlsafe(48),
            "KAYDBOOKS_CONNECTOR_SECRET": secrets.token_urlsafe(48),
        },
    )
    return {
        "status": "created-unbound",
        "company": company,
        "production_posting": False,
        "sample_posting": False,
        "next": "review company identity, configure mappings and qualify read-only connection",
    }


def _write(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, indent=2)
        file.write("\n")


def inspect_setup(
    config_path, company_id, target_path, credential_path=None, *, principal, connector_id
) -> dict:
    """Inspect config and credential shape without loading secrets into the environment.

    Results are scoped to the requested company and contain no paths, names or tokens.
    Offline configuration readiness never establishes a live connection or write approval.
    """
    config = Config.load(private_path(config_path))
    target = target_values(read_json(target_path))
    if company_id not in config.companies or target["company_id"] != company_id:
        raise BridgeError("target and explicit company must match configuration")
    policy = config.companies[company_id]
    connector = config.connectors.get(connector_id)
    operator = config.principals.get(principal)
    if connector is None or connector.company != company_id or operator is None:
        raise BridgeError("explicit principal and company connector required")
    credentials = read_json(credential_path) if credential_path else {}
    if not isinstance(credentials, dict):
        raise BridgeError("credential object required")
    env_names = {connector.password_env, operator["token_env"]}
    available = all(
        isinstance(credentials.get(name), str)
        and len(credentials[name]) >= 32
        and not any(c in credentials[name] for c in "\r\n\0")
        for name in env_names
    ) and bool(env_names)
    checks = {
        "company_file_exists": Path(target["company_file"]).is_file(),
        "principal_read_granted": "read" in operator["companies"].get(company_id, []),
        "identity_binding_configured": connector.identity_sha256 != "0" * 64,
        "credentials_available": available,
        "credentials_distinct": available
        and len({credentials[name] for name in env_names}) == len(env_names),
        "account_roles_configured": bool(policy.account_roles),
        "invoice_masters_configured": bool(policy.invoice_masters),
    }
    return {
        "company": company_id,
        "checks": checks,
        "configuration_complete": all(checks.values()),
        "pending": [name for name, passed in checks.items() if not passed],
        "live_connection_verified": False,
        "company_identity_verified": False,
        "production_posting": False,
        "sample_gate_configured": bool(policy.sample_posting),
        "scope": "offline configuration only; live identity, grants and fresh evidence still required",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--request", required=True, type=Path)
    init.add_argument("--destination", required=True, type=Path)
    check = commands.add_parser("check")
    check.add_argument("--config", required=True, type=Path)
    check.add_argument("--company", required=True)
    check.add_argument("--principal", required=True)
    check.add_argument("--connector", required=True)
    check.add_argument("--target", required=True, type=Path)
    check.add_argument("--credentials", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.request, args.destination)
            code = 0
        else:
            result = inspect_setup(
                args.config,
                args.company,
                args.target,
                args.credentials,
                principal=args.principal,
                connector_id=args.connector,
            )
            code = 0 if result["configuration_complete"] else 1
        print(json.dumps(result, indent=2))
        return code
    except BridgeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        print(json.dumps({"error": "invalid or inaccessible private setup input"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
