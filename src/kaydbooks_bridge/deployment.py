"""Fail-closed deployment surface for read-only QBWC qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from qbwc_kit.qbxml import parse_response

from .config import BridgeError, Config, outside_repository, strict_keys
from .qbwc import DurableQBWCDiscoveryService, company_identity_digest
from .store import Store


def _private_file(value: str, label: str) -> Path:
    path = outside_repository(Path(value))
    if not path.is_absolute() or not path.is_file():
        raise BridgeError(f"private {label} file is unavailable")
    return path


def _https_url(value: object, label: str, *, endpoint: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise BridgeError(f"invalid {label} URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BridgeError(f"invalid {label} URL")
    if endpoint and parsed.path != "/qbwc":
        raise BridgeError("QBWC endpoint URL must end in /qbwc")
    return value


def _guid(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", value):
        raise BridgeError(f"invalid {label}")
    try:
        parsed = UUID(value[1:-1])
    except ValueError as exc:
        raise BridgeError(f"invalid {label}") from exc
    return "{" + str(parsed).upper() + "}"


@dataclass(frozen=True)
class QWCProfile:
    app_name: str
    endpoint_url: str
    support_url: str
    username: str
    owner_id: str
    file_id: str
    run_every_seconds: int
    auth_flags: int
    is_read_only: bool
    unattended_mode_pref: str
    app_unique_name: str

    @classmethod
    def load(cls, path: str | Path) -> QWCProfile:
        source = outside_repository(Path(path))
        data = json.loads(source.read_text(encoding="utf-8"))
        strict_keys(
            data,
            {
                "schema_version",
                "app_name",
                "endpoint_url",
                "support_url",
                "username",
                "owner_id",
                "file_id",
                "run_every_seconds",
                "auth_flags",
                "is_read_only",
                "unattended_mode_pref",
                "app_unique_name",
            },
        )
        if data["schema_version"] != 1:
            raise BridgeError("unsupported QWC profile version")
        if not isinstance(data["app_name"], str) or not 1 <= len(data["app_name"]) <= 80:
            raise BridgeError("invalid QWC application name")
        if not isinstance(data["username"], str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", data["username"]
        ):
            raise BridgeError("invalid QWC connector username")
        interval = data["run_every_seconds"]
        if type(interval) is not int or not 60 <= interval <= 31_556_760:
            raise BridgeError("invalid QWC schedule interval")
        flags = data["auth_flags"]
        if type(flags) is not int or not 0 <= flags <= 0xF:
            raise BridgeError("invalid QWC edition flags")
        if data["is_read_only"] is not True:
            raise BridgeError("qualification QWC must require QuickBooks read-only access")
        unattended = data["unattended_mode_pref"]
        if unattended != "umpOptional":
            raise BridgeError("qualification QWC must make unattended access optional")
        unique_name = data["app_unique_name"]
        if not isinstance(unique_name, str) or not re.fullmatch(
            r"[A-Za-z0-9._ -]{1,80}", unique_name
        ):
            raise BridgeError("invalid QWC unique application name")
        return cls(
            app_name=data["app_name"],
            endpoint_url=_https_url(data["endpoint_url"], "endpoint", endpoint=True),
            support_url=_https_url(data["support_url"], "support"),
            username=data["username"],
            owner_id=_guid(data["owner_id"], "OwnerID"),
            file_id=_guid(data["file_id"], "FileID"),
            run_every_seconds=interval,
            auth_flags=flags,
            is_read_only=True,
            unattended_mode_pref=unattended,
            app_unique_name=unique_name,
        )

    def render(self) -> str:
        from qbwc_kit.wsdl import build_qwc

        return build_qwc(
            app_name=self.app_name,
            app_id="",
            app_url=self.endpoint_url,
            app_description="Read-only QuickBooks company qualification; posting disabled",
            username=self.username,
            owner_id=self.owner_id,
            file_id=self.file_id,
            run_every_n_seconds=self.run_every_seconds,
            support_url=self.support_url,
            auth_flags=self.auth_flags,
            is_read_only=self.is_read_only,
            unattended_mode_pref=self.unattended_mode_pref,
            app_display_name=self.app_name,
            app_unique_name=self.app_unique_name,
        )

    def write_stable(self, destination: str | Path) -> Path:
        target = outside_repository(Path(destination))
        if target.suffix.lower() != ".qwc":
            raise BridgeError("QWC output must use the .qwc extension")
        content = self.render() + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != content:
                raise BridgeError("existing QWC differs; preserve stable IDs or choose a new file")
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target


def load_secret_file(path: str | Path) -> None:
    source = _private_file(str(path), "credential")
    values = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or not values:
        raise BridgeError("invalid private credential file")
    for name, value in values.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"KAYDBOOKS_[A-Z0-9_]+", name)
            or not isinstance(value, str)
            or len(value) < 32
            or any(char in value for char in "\r\n\0")
        ):
            raise BridgeError("invalid private credential file")
    os.environ.update(values)


def export_binding_candidate(
    config_path: str | Path, connector_name: str, destination: str | Path
) -> Path:
    """Export observed HCP claims for explicit operator review; never change a binding."""
    config = Config.load(config_path)
    connector = config.connectors.get(connector_name)
    if connector is None:
        raise BridgeError("unknown connector")
    store = Store(config.root, connector.company)
    with store.transaction() as db:
        row = db.execute(
            """SELECT hcp_xml,hcp_hash,created_at FROM qbwc_sessions
               WHERE connector=? AND hcp_xml IS NOT NULL
               ORDER BY created_at DESC LIMIT 1""",
            (connector_name,),
        ).fetchone()
    if row is None:
        raise BridgeError("no observed company candidate is available")
    responses = parse_response(row["hcp_xml"])
    matches = [response for response in responses if response.entity == "Company"]
    if len(matches) != 1 or not matches[0].ok or len(matches[0].records) != 1:
        raise BridgeError("observed company candidate is ambiguous or invalid")
    company = matches[0].records[0]
    claims = {}
    for field in connector.identity_fields:
        value = company
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value:
                raise BridgeError("observed company candidate is incomplete")
            value = value[part]
        if not isinstance(value, str) or not value.strip():
            raise BridgeError("observed company candidate is incomplete")
        claims[field] = value.strip()
    evidence = {
        "schema_version": 1,
        "company": connector.company,
        "connector": connector.id,
        "operator_confirmed": False,
        "identity_fields": list(connector.identity_fields),
        "identity_sha256": company_identity_digest(company, connector.identity_fields),
        "claims": claims,
        "hcp_sha256": row["hcp_hash"],
        "observed_at": row["created_at"],
    }
    target = outside_repository(Path(destination))
    if target.exists():
        raise BridgeError("binding candidate output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return target


def create_staging_app(config_path: str | Path, endpoint_url: str, max_bytes: int = 8_388_608):
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 16_777_216:
        raise BridgeError("invalid callback size limit")
    endpoint_url = _https_url(endpoint_url, "endpoint", endpoint=True)
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError("install the server extra to run QBWC staging") from exc
    from qbwc_kit.server import create_app

    app = FastAPI(title="KaydBooks Bridge QBWC qualification", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def health():
        return {"status": "ready", "mode": "read-only-discovery", "live_posting": False}

    @app.get("/support")
    def support():
        return {"service": "KaydBooks Bridge", "mode": "read-only qualification"}

    service = DurableQBWCDiscoveryService.from_path(config_path)
    from .web_ui import install

    install(app, config_path, endpoint_url)
    return create_app(
        service,
        endpoint_url=endpoint_url,
        app=app,
        max_request_bytes=max_bytes,
    )


def app_from_environment():
    credential_file = os.environ.get("KAYDBOOKS_QBWC_SECRET_FILE")
    if credential_file:
        load_secret_file(credential_file)
    config = os.environ.get("KAYDBOOKS_CONFIG", "")
    endpoint = os.environ.get("KAYDBOOKS_QBWC_ENDPOINT", "")
    if not config or not endpoint:
        raise BridgeError("private config and HTTPS endpoint are required")
    return create_staging_app(config, endpoint)


def serve() -> int:
    try:
        credential_file = os.environ.get("KAYDBOOKS_QBWC_SECRET_FILE")
        if credential_file:
            load_secret_file(credential_file)
        config = os.environ.get("KAYDBOOKS_CONFIG", "")
        endpoint = os.environ.get("KAYDBOOKS_QBWC_ENDPOINT", "")
        cert = _private_file(os.environ.get("KAYDBOOKS_QBWC_CERTFILE", ""), "certificate")
        key = _private_file(os.environ.get("KAYDBOOKS_QBWC_KEYFILE", ""), "key")
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
        app = create_staging_app(config, endpoint)
        import uvicorn

        uvicorn.run(
            app,
            host=os.environ.get("KAYDBOOKS_QBWC_HOST", "127.0.0.1"),
            port=int(os.environ.get("KAYDBOOKS_QBWC_PORT", "8443")),
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            access_log=False,
            server_header=False,
        )
        return 0
    except (BridgeError, OSError, ValueError, TypeError):
        print(
            json.dumps({"error": "invalid or inaccessible private staging input"}), file=sys.stderr
        )
        return 2


def profile_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private QBWC qualification configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate-qwc")
    candidate = commands.add_parser("export-binding-candidate")
    candidate.add_argument("--connector", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-qwc":
            profile = QWCProfile.load(os.environ.get("KAYDBOOKS_QBWC_PROFILE", ""))
            profile.write_stable(os.environ.get("KAYDBOOKS_QBWC_QWC_FILE", ""))
            result = {"status": "ready", "live_posting": False}
        else:
            export_binding_candidate(
                os.environ.get("KAYDBOOKS_CONFIG", ""),
                args.connector,
                os.environ.get("KAYDBOOKS_QBWC_BINDING_CANDIDATE", ""),
            )
            result = {"status": "candidate-exported", "operator_confirmed": False}
        print(json.dumps(result))
        return 0
    except (BridgeError, OSError, ValueError, TypeError, KeyError):
        print(
            json.dumps({"error": "invalid or inaccessible private qualification input"}),
            file=sys.stderr,
        )
        return 2
