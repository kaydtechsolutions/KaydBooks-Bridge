"""Durable, read-only QBWC discovery callbacks.

This service deliberately has no task injection or write request API.  It emits one
fixed HostQuery/CompanyQuery batch, optionally extended by an authenticated bounded
account or invoice master check, and verifies CompanyRet evidence before accepting results.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qbwc_kit import soap
from qbwc_kit.qbxml import OnError, QBXMLRequest, parse_response, query
from qbwc_kit.service import MIN_CLIENT_VERSION, SERVER_VERSION, _parse_version

from .config import BridgeError, Config, Connector
from .store import Store
from .validation import canonical, digest

logger = logging.getLogger("kaydbooks_bridge.qbwc")

INVALID_USER = "nvu"
BUSY = "busy"
ACTIVE_STATES = ("authenticated", "request-sent", "verified", "blocked")
DISCOVERY_MIN_VERSION = {"US": (1, 0), "CA": (2, 0), "UK": (2, 0), "AU": (6, 1)}
UNCONFIRMED_IDENTITY = "0" * 64


class UnknownTicket(LookupError):
    pass


def company_identity_digest(record: dict, fields: tuple[str, ...]) -> str:
    """Hash an exact, configured subset of CompanyRet without using a file path."""
    claims: dict[str, str] = {}
    for path in fields:
        value: object = record
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise BridgeError("company identity evidence is incomplete")
            value = value[part]
        if not isinstance(value, str) or not value.strip():
            raise BridgeError("company identity evidence is incomplete")
        claims[path] = value.strip()
    return digest({"schema": "qbdesktop-company-identity-v1", "claims": claims})


@dataclass
class DurableQBWCDiscoveryService:
    """QBWC callback implementation backed by each company's private SQLite store."""

    config: Config
    ttl_seconds: float = 3600.0
    clock: Callable[[], float] = time.time
    server_version: str = SERVER_VERSION
    min_client_version: tuple[int, ...] = MIN_CLIENT_VERSION

    @classmethod
    def from_path(cls, path: str | Path, **kwargs) -> DurableQBWCDiscoveryService:
        return cls(Config.load(path), **kwargs)

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._stores = {
            company: Store(self.config.root, company) for company in self.config.companies
        }

    def dispatch(self, body: str | bytes) -> str:
        try:
            call = soap.parse_request(body)
        except soap.SoapError as exc:
            return soap.build_fault(str(exc), code="soap:Client")
        handler = getattr(self, f"_do_{call.method}", None)
        if handler is None:
            return soap.build_fault(f"unsupported method {call.method!r}", code="soap:Client")
        try:
            result = handler(call)
        except UnknownTicket:
            result = self._unknown_ticket_result(call.method)
        except Exception as exc:  # noqa: BLE001 - SOAP fault is the protocol error channel
            logger.exception("durable QBWC callback failed: %s", call.method)
            return soap.build_fault(f"{type(exc).__name__}: {exc}")
        return soap.build_response(call.method, result)

    @staticmethod
    def _unknown_ticket_result(method: str) -> str | int:
        if method == "sendRequestXML":
            return ""
        if method == "receiveResponseXML":
            return -1
        if method == "getLastError":
            return "session expired"
        if method == "closeConnection":
            return "OK"
        return "done"

    def _do_serverVersion(self, call: soap.SoapCall) -> str:
        return self.server_version

    def _do_clientVersion(self, call: soap.SoapCall) -> str:
        raw = call.get("strVersion") or call.positional(0)
        if raw and _parse_version(raw) < self.min_client_version:
            wanted = ".".join(str(part) for part in self.min_client_version)
            return f"E:Web Connector {raw} is too old; {wanted} or newer is required"
        return ""

    def _do_authenticate(self, call: soap.SoapCall) -> list[str]:
        username = call.get("strUserName") or call.positional(0)
        password = call.get("strPassword") or call.positional(1)
        try:
            connector = self.config.authenticate_connector(username, password)
            company_file = self.config.connector_company_file(connector)
        except BridgeError:
            return ["", INVALID_USER]

        now = float(self.clock())
        store = self._stores[connector.company]
        with store.transaction() as db:
            self._expire_active(db, store, now)
            if db.execute(
                "SELECT 1 FROM native_invoice_attempts n JOIN jobs j ON j.id=n.job_id WHERE j.state IN ('in-flight','unknown')"
            ).fetchone():
                store.event(
                    db, now, f"qbwc:{connector.id}", None, "qbwc_native_write_overlap_blocked", {}
                )
                return ["", BUSY]
            if db.execute(
                "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
            ).fetchone():
                store.event(db, now, f"qbwc:{connector.id}", None, "qbwc_sdk_overlap_blocked", {})
                return ["", BUSY]
            active = db.execute(
                f"SELECT * FROM qbwc_sessions WHERE state IN ({','.join('?' * len(ACTIVE_STATES))})",
                ACTIVE_STATES,
            ).fetchone()
            if active is not None:
                if active["connector"] != connector.id:
                    self._callback(
                        db,
                        now,
                        active["ticket"],
                        "authenticate",
                        {"connector": connector.id},
                        ["", BUSY],
                        "overlap-blocked",
                    )
                    store.event(
                        db,
                        now,
                        f"qbwc:{connector.id}",
                        None,
                        "qbwc_session_overlap_blocked",
                        {"active_connector": active["connector"]},
                    )
                    return ["", BUSY]
                result = [active["ticket"], company_file]
                self._callback(
                    db,
                    now,
                    active["ticket"],
                    "authenticate",
                    {"connector": connector.id},
                    result,
                    "duplicate",
                )
                store.event(
                    db,
                    now,
                    f"qbwc:{connector.id}",
                    None,
                    "qbwc_authenticate_repeated",
                    {"state": active["state"]},
                )
                return result

            ticket = secrets.token_urlsafe(32)
            correlation = str(secrets.randbelow(900_000_000) + 100_000_000)
            db.execute(
                """INSERT INTO qbwc_sessions
                   (ticket,connector,state,created_at,updated_at,expires_at,correlation)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    ticket,
                    connector.id,
                    "authenticated",
                    now,
                    now,
                    now + self.ttl_seconds,
                    correlation,
                ),
            )
            db.execute(
                "UPDATE qbwc_account_jobs SET ticket=? WHERE connector=? AND ticket IS NULL",
                (ticket, connector.id),
            )
            db.execute(
                "UPDATE qbwc_invoice_jobs SET ticket=? WHERE connector=? AND ticket IS NULL",
                (ticket, connector.id),
            )
            result = [ticket, company_file]
            self._callback(
                db,
                now,
                ticket,
                "authenticate",
                {"connector": connector.id},
                result,
                "created",
            )
            store.event(
                db,
                now,
                f"qbwc:{connector.id}",
                None,
                "qbwc_session_authenticated",
                {"expires_at": now + self.ttl_seconds},
            )
            return result

    def _do_sendRequestXML(self, call: soap.SoapCall) -> str:
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        now = float(self.clock())
        hcp = call.get("strHCPResponse")
        company_file_hash = digest({"company_file_callback": call.get("strCompanyFileName", "")})
        country = call.get("qbXMLCountry")
        raw_major = call.get("qbXMLMajorVers")
        raw_minor = call.get("qbXMLMinorVers")
        callback_input = {
            "ticket": digest({"ticket": ticket}),
            "hcp_hash": digest({"hcp": hcp}) if hcp else None,
            "company_file_hash": company_file_hash,
            "country": country,
            "major": raw_major,
            "minor": raw_minor,
        }
        with store.transaction() as db:
            row = self._live_row(db, store, ticket, now)
            connector = self.config.connectors[row["connector"]]
            if row["state"] in ("expired", "closed", "disconnected", "blocked", "verified"):
                self._callback(db, now, ticket, "sendRequestXML", callback_input, "", row["state"])
                return ""

            lookup = db.execute(
                "SELECT * FROM qbwc_account_jobs WHERE ticket=?", (ticket,)
            ).fetchone()
            if lookup:
                try:
                    self.config.authorize(lookup["actor"], connector.company, "read")
                except BridgeError:
                    self._block(db, store, row, now, "account lookup permission revoked")
                    return ""

            invoice = db.execute(
                "SELECT * FROM qbwc_invoice_jobs WHERE ticket=?", (ticket,)
            ).fetchone()
            check = None
            if invoice:
                try:
                    from .qbwc_invoices import current_plan

                    if lookup:
                        raise BridgeError("conflicting read jobs for session")
                    check = current_plan(self, invoice, connector)
                except BridgeError as exc:
                    self._block(db, store, row, now, str(exc))
                    self._callback(
                        db,
                        now,
                        ticket,
                        "sendRequestXML",
                        callback_input,
                        "",
                        "invoice-context-blocked",
                    )
                    return ""

            if row["state"] == "request-sent":
                try:
                    repeated_version = self._callback_version(raw_major, raw_minor, country)
                except BridgeError:
                    repeated_version = "invalid"
                mismatch = (
                    row["company_file_hash"] != company_file_hash
                    or row["country"] != country
                    or row["qbxml_version"] != repeated_version
                    or (hcp and row["hcp_hash"] != digest({"hcp": hcp}))
                )
                if mismatch:
                    self._block(
                        db, store, row, now, "repeated sendRequestXML changed session context"
                    )
                    self._callback(
                        db, now, ticket, "sendRequestXML", callback_input, "", "context-conflict"
                    )
                    return ""
                db.execute(
                    """UPDATE qbwc_sessions SET request_return_count=request_return_count+1,
                       updated_at=? WHERE ticket=?""",
                    (now, ticket),
                )
                request = row["request_xml"]
                self._callback(
                    db, now, ticket, "sendRequestXML", callback_input, request, "duplicate"
                )
                store.event(
                    db,
                    now,
                    f"qbwc:{connector.id}",
                    None,
                    "qbwc_request_repeated",
                    {"request_hash": row["request_hash"]},
                )
                return request

            hcp_hash = digest({"hcp": hcp}) if hcp else None
            db.execute(
                """UPDATE qbwc_sessions SET updated_at=?,hcp_xml=?,hcp_hash=?,
                   company_file_hash=?,country=?,qbxml_version=? WHERE ticket=?""",
                (
                    now,
                    hcp or None,
                    hcp_hash,
                    company_file_hash,
                    country,
                    f"{raw_major}.{raw_minor}",
                    ticket,
                ),
            )
            try:
                version = self._callback_version(raw_major, raw_minor, country)
                db.execute(
                    "UPDATE qbwc_sessions SET qbxml_version=? WHERE ticket=?",
                    (version, ticket),
                )
                if hcp:
                    self._verify_hcp(hcp, connector)
                request = self._discovery_request(row["correlation"], version)
                if lookup:
                    if not hcp or country != "US" or tuple(map(int, version.split("."))) < (4, 0):
                        raise BridgeError("account lookup requires verified HCP and US qbXML 4+")
                    from .account_lookup import append_query

                    request = append_query(request, row["correlation"], version, lookup["list_id"])
                if check is not None:
                    if not hcp or country != "US" or version != "17.0":
                        raise BridgeError("invoice check requires verified HCP and US qbXML 17.0")
                    from .qbwc_invoices import append_request

                    request = append_request(request, row["correlation"], check)
            except (BridgeError, ValueError, TypeError) as exc:
                self._block(db, store, row, now, str(exc))
                self._callback(
                    db, now, ticket, "sendRequestXML", callback_input, "", "discovery-blocked"
                )
                return ""

            request_hash = digest({"request": request})
            db.execute(
                """UPDATE qbwc_sessions SET state='request-sent',updated_at=?,request_xml=?,
                   request_hash=?,request_return_count=1 WHERE ticket=?""",
                (
                    now,
                    request,
                    request_hash,
                    ticket,
                ),
            )
            self._callback(db, now, ticket, "sendRequestXML", callback_input, request, "sent")
            store.event(
                db,
                now,
                f"qbwc:{connector.id}",
                None,
                "qbwc_discovery_request_persisted",
                {
                    "request_hash": request_hash,
                    "hcp_hash": hcp_hash,
                    "country": country,
                    "qbxml_version": version,
                },
            )
            return request

    def _do_receiveResponseXML(self, call: soap.SoapCall) -> int:
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        now = float(self.clock())
        response = call.get("response") or call.positional(1)
        hresult = call.get("hresult")
        message = call.get("message")
        input_hash = digest({"response": response, "hresult": hresult, "message": message})
        with store.transaction() as db:
            row = self._live_row(db, store, ticket, now)
            connector = self.config.connectors[row["connector"]]
            callback_input = {"callback_hash": input_hash}
            invoice = db.execute(
                "SELECT * FROM qbwc_invoice_jobs WHERE ticket=?", (ticket,)
            ).fetchone()
            check = None
            if invoice:
                try:
                    from .qbwc_invoices import current_plan

                    check = current_plan(self, invoice, connector)
                except BridgeError as exc:
                    self._block(db, store, row, now, str(exc))
                    self._callback(
                        db,
                        now,
                        ticket,
                        "receiveResponseXML",
                        callback_input,
                        -1,
                        "invoice-context-blocked",
                    )
                    return -1
            if row["response_hash"] is not None:
                if row["response_hash"] == input_hash:
                    db.execute(
                        """UPDATE qbwc_sessions SET response_callback_count=response_callback_count+1,
                           updated_at=? WHERE ticket=?""",
                        (now, ticket),
                    )
                    result = int(row["response_result"])
                    self._callback(
                        db, now, ticket, "receiveResponseXML", callback_input, result, "duplicate"
                    )
                    store.event(
                        db,
                        now,
                        f"qbwc:{connector.id}",
                        None,
                        "qbwc_response_repeated",
                        {"response_hash": input_hash, "result": result},
                    )
                    return result
                if row["state"] in ("request-sent", "verified"):
                    self._block(db, store, row, now, "conflicting repeated response callback")
                else:
                    db.execute(
                        "UPDATE qbwc_sessions SET last_error=?,updated_at=? WHERE ticket=?",
                        ("conflicting repeated response callback", now, ticket),
                    )
                self._callback(
                    db, now, ticket, "receiveResponseXML", callback_input, -1, "conflict"
                )
                return -1

            if row["state"] != "request-sent":
                if row["state"] in ("authenticated", "verified"):
                    self._block(db, store, row, now, "receiveResponseXML was out of sequence")
                self._callback(
                    db, now, ticket, "receiveResponseXML", callback_input, -1, "out-of-sequence"
                )
                return -1

            result = -1
            state = "blocked"
            identity_hash = None
            host_evidence = None
            error = ""
            if hresult:
                error = f"QuickBooks request processor error {hresult}"
            else:
                try:
                    payload = response
                    lookup = db.execute(
                        "SELECT * FROM qbwc_account_jobs WHERE ticket=?", (ticket,)
                    ).fetchone()
                    if lookup:
                        from .account_lookup import validate_response

                        self.config.authorize(lookup["actor"], connector.company, "read")
                        payload, _ = validate_response(
                            response, row["correlation"], lookup["list_id"]
                        )
                    if check is not None:
                        from .qbwc_invoices import check_response

                        if lookup:
                            raise BridgeError("conflicting read jobs for session")
                        payload, _ = check_response(response, row["correlation"], check)
                    identity_hash, host_evidence = self._verify_discovery_response(
                        payload, row, connector
                    )
                    state = "verified"
                    result = 100
                except (BridgeError, ValueError, KeyError) as exc:
                    error = str(exc)

            db.execute(
                """UPDATE qbwc_sessions SET state=?,updated_at=?,response_xml=?,response_hash=?,
                   response_result=?,response_callback_count=1,identity_hash=?,host_evidence=?,
                   last_error=? WHERE ticket=?""",
                (
                    state,
                    now,
                    response,
                    input_hash,
                    result,
                    identity_hash,
                    canonical(host_evidence) if host_evidence else None,
                    error,
                    ticket,
                ),
            )
            self._callback(
                db,
                now,
                ticket,
                "receiveResponseXML",
                callback_input,
                result,
                "company-bound" if result == 100 else "blocked",
            )
            store.event(
                db,
                now,
                f"qbwc:{connector.id}",
                None,
                "qbwc_company_binding_verified"
                if result == 100
                else "qbwc_company_binding_blocked",
                {
                    "response_hash": input_hash,
                    "identity_hash": identity_hash,
                    "result": result,
                    "reason": error,
                },
            )
            return result

    def _do_connectionError(self, call: soap.SoapCall) -> str:
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        now = float(self.clock())
        hresult = call.get("hresult")
        with store.transaction() as db:
            row = self._live_row(db, store, ticket, now)
            if row["state"] in ("authenticated", "request-sent"):
                db.execute(
                    """UPDATE qbwc_sessions SET state='disconnected',updated_at=?,last_error=?
                       WHERE ticket=?""",
                    (now, f"QuickBooks connection error {hresult}", ticket),
                )
            self._callback(
                db,
                now,
                ticket,
                "connectionError",
                {"hresult": hresult, "message_hash": digest({"message": call.get("message")})},
                "done",
                "disconnected",
            )
            store.event(
                db,
                now,
                f"qbwc:{row['connector']}",
                None,
                "qbwc_connection_ended",
                {"hresult": hresult},
            )
        return "done"

    def _do_getLastError(self, call: soap.SoapCall) -> str:
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        now = float(self.clock())
        with store.transaction() as db:
            row = self._live_row(db, store, ticket, now)
            result = row["last_error"] or "no error recorded"
            self._callback(db, now, ticket, "getLastError", {}, result, "reported")
            return result

    def _do_closeConnection(self, call: soap.SoapCall) -> str:
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        now = float(self.clock())
        with store.transaction() as db:
            row = self._live_row(db, store, ticket, now)
            result = row["close_result"] or (
                "OK" if not row["last_error"] else "Completed with errors"
            )
            if row["state"] not in ("closed", "expired"):
                db.execute(
                    """UPDATE qbwc_sessions SET state='closed',updated_at=?,close_result=?
                       WHERE ticket=?""",
                    (now, result, ticket),
                )
            elif row["close_result"] is None:
                db.execute(
                    "UPDATE qbwc_sessions SET updated_at=?,close_result=? WHERE ticket=?",
                    (now, result, ticket),
                )
            self._callback(db, now, ticket, "closeConnection", {}, result, "closed")
            store.event(
                db,
                now,
                f"qbwc:{row['connector']}",
                None,
                "qbwc_session_closed",
                {"result": result},
            )
            return result

    def inspect_session(self, ticket: str) -> dict:
        """Return private durable state for operations/tests; never logs response bodies."""
        store = self._locate(ticket)
        with store.transaction() as db:
            row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (ticket,)).fetchone()
            if row is None:
                raise UnknownTicket(ticket)
            return dict(row)

    def _locate(self, ticket: str) -> Store:
        if not ticket:
            raise UnknownTicket(ticket)
        for store in self._stores.values():
            with store.transaction() as db:
                if db.execute("SELECT 1 FROM qbwc_sessions WHERE ticket=?", (ticket,)).fetchone():
                    return store
        raise UnknownTicket(ticket)

    def _live_row(self, db, store: Store, ticket: str, now: float):
        row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (ticket,)).fetchone()
        if row is None:
            raise UnknownTicket(ticket)
        if row["state"] in ACTIVE_STATES and now >= row["expires_at"]:
            db.execute(
                """UPDATE qbwc_sessions SET state='expired',updated_at=?,
                   last_error='session expired' WHERE ticket=?""",
                (now, ticket),
            )
            store.event(
                db,
                now,
                f"qbwc:{row['connector']}",
                None,
                "qbwc_session_expired",
                {},
            )
            row = db.execute("SELECT * FROM qbwc_sessions WHERE ticket=?", (ticket,)).fetchone()
        return row

    def _expire_active(self, db, store: Store, now: float) -> None:
        rows = db.execute(
            f"""SELECT * FROM qbwc_sessions WHERE state IN
                ({",".join("?" * len(ACTIVE_STATES))}) AND expires_at<=?""",
            (*ACTIVE_STATES, now),
        ).fetchall()
        for row in rows:
            db.execute(
                """UPDATE qbwc_sessions SET state='expired',updated_at=?,
                   last_error='session expired' WHERE ticket=?""",
                (now, row["ticket"]),
            )
            store.event(
                db,
                now,
                f"qbwc:{row['connector']}",
                None,
                "qbwc_session_expired",
                {},
            )

    @staticmethod
    def _callback(db, at, ticket, method, callback_input, result, outcome) -> None:
        db.execute(
            """INSERT INTO qbwc_callbacks
               (at,ticket,method,input_hash,result_hash,outcome) VALUES (?,?,?,?,?,?)""",
            (at, ticket, method, digest(callback_input), digest(result), outcome),
        )

    @staticmethod
    def _block(db, store: Store, row, now: float, reason: str) -> None:
        reason = reason[:500]
        db.execute(
            "UPDATE qbwc_sessions SET state='blocked',updated_at=?,last_error=? WHERE ticket=?",
            (now, reason, row["ticket"]),
        )
        store.event(
            db,
            now,
            f"qbwc:{row['connector']}",
            None,
            "qbwc_session_blocked",
            {"reason": reason},
        )

    @staticmethod
    def _discovery_request(correlation: str, version: str) -> str:
        return QBXMLRequest(
            [
                query("Host", request_id=f"{correlation}1"),
                query("Company", request_id=f"{correlation}2"),
            ],
            on_error=OnError.CONTINUE,
            version=version,
        ).render()

    @staticmethod
    def _callback_version(raw_major: str, raw_minor: str, country: str) -> str:
        try:
            major, minor = int(raw_major), int(raw_minor)
        except (TypeError, ValueError) as exc:
            raise BridgeError("unsupported qbXML callback version") from exc
        if not (1 <= major <= 99 and 0 <= minor <= 99):
            raise BridgeError("unsupported qbXML callback version")
        minimum = DISCOVERY_MIN_VERSION.get(country)
        if minimum is None or (major, minor) < minimum:
            raise BridgeError("CompanyQuery is unsupported for this country/version")
        return f"{major}.{minor}"

    @staticmethod
    def _response(response_set, name: str, request_id: str | None = None):
        matches = [response for response in response_set if response.entity == name]
        if len(matches) != 1:
            raise BridgeError(f"discovery requires exactly one {name} response")
        result = matches[0]
        result.raise_for_status()
        if request_id is not None and result.request_id != request_id:
            raise BridgeError(f"{name} response correlation mismatch")
        if len(result.records) != 1:
            raise BridgeError(f"discovery requires exactly one {name} record")
        return result.records[0]

    def _verify_hcp(self, payload: str, connector: Connector) -> None:
        if connector.identity_sha256 == UNCONFIRMED_IDENTITY:
            raise BridgeError("company binding is not operator-confirmed")
        if "currently not supported in QBPOS" in payload:
            raise BridgeError("QuickBooks Point of Sale discovery is unsupported")
        try:
            responses = parse_response(payload)
            company = self._response(responses, "Company")
            observed = company_identity_digest(company, connector.identity_fields)
        except Exception as exc:  # noqa: BLE001 - fail the untrusted callback closed
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("invalid HCP discovery response") from exc
        if observed != connector.identity_sha256:
            raise BridgeError("HCP company binding mismatch")

    @classmethod
    def _verify_discovery_response(cls, payload: str, row, connector: Connector):
        if connector.identity_sha256 == UNCONFIRMED_IDENTITY:
            raise BridgeError("company binding is not operator-confirmed")
        try:
            responses = parse_response(payload)
            if len(responses) != 2:
                raise BridgeError("discovery response count mismatch")
            host = cls._response(responses, "Host", f"{row['correlation']}1")
            company = cls._response(responses, "Company", f"{row['correlation']}2")
        except Exception as exc:  # noqa: BLE001 - fail the untrusted callback closed
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("invalid discovery response") from exc

        required_host = ("ProductName", "MajorVersion", "MinorVersion", "Country")
        if any(
            not isinstance(host.get(field), str) or not host[field].strip()
            for field in required_host
        ):
            raise BridgeError("host discovery evidence is incomplete")
        supported = host.get("SupportedQBXMLVersion")
        versions = supported if isinstance(supported, list) else [supported]
        if not versions or any(not isinstance(value, str) or not value for value in versions):
            raise BridgeError("supported qbXML versions are missing")
        if row["qbxml_version"] not in versions:
            raise BridgeError("negotiated qbXML version is not supported by this host")
        if row["country"] != host["Country"]:
            raise BridgeError("QuickBooks country evidence mismatch")

        observed = company_identity_digest(company, connector.identity_fields)
        if observed != connector.identity_sha256:
            raise BridgeError("configured company binding mismatch")
        host_evidence = {
            "ProductName": host["ProductName"],
            "MajorVersion": host["MajorVersion"],
            "MinorVersion": host["MinorVersion"],
            "Country": host["Country"],
            "SupportedQBXMLVersion": versions,
            "IsAutomaticLogin": host.get("IsAutomaticLogin"),
            "QBFileMode": host.get("QBFileMode"),
        }
        return observed, host_evidence
