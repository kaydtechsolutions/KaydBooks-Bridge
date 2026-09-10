"""Composio stays company-scoped, approved, durable and at-most-once."""

# ruff: noqa: F811
import json

import pytest

from kaydbooks_bridge import composio
from kaydbooks_bridge.config import BridgeError
from test_bridge import TOKENS, setup  # noqa: F401

WRITE_TOOL = "GMAIL_SEND_EMAIL"
READ_TOOL = "GOOGLECALENDAR_EVENTS_LIST"


def test_direct_transport_uses_current_v31_endpoint_and_pinned_payload(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _):
            return b'{"successful":true,"log_id":"log_synthetic_transport"}'

    def urlopen(request, timeout):
        seen.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
        assert request.get_header("X-api-key") == "synthetic-key"
        return Response()

    monkeypatch.setattr(composio.urllib.request, "urlopen", urlopen)
    payload = {
        "arguments": {"calendar_id": "synthetic"},
        "user_id": "kaydbooks-synthetic",
        "connected_account_id": "ca_synthetic",
        "version": "20260817_00",
    }
    result = composio.http_execute(READ_TOOL, payload, "synthetic-key")
    assert seen == {
        "url": "https://backend.composio.dev/api/v3.1/tools/execute/" + READ_TOOL,
        "body": payload,
        "timeout": 30,
    }
    assert result["successful"] is True


@pytest.fixture
def policy(setup, tmp_path, monkeypatch):
    raw = json.loads(setup[1].read_text())
    raw["principals"]["preparer-a"]["companies"]["company-a"].append("approve")
    setup[1].write_text(json.dumps(raw))
    path = tmp_path / "composio-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "companies": {
                    "company-a": {
                        "user_id": "kaydbooks-company-a",
                        "tools": {
                            WRITE_TOOL: {
                                "version": "20260817_00",
                                "mode": "write",
                                "connected_account_id_env": "KAYDBOOKS_COMPOSIO_COMPANY_A_GMAIL",
                            },
                            READ_TOOL: {
                                "version": "20260817_00",
                                "mode": "read",
                                "connected_account_id_env": "KAYDBOOKS_COMPOSIO_COMPANY_A_CALENDAR",
                            },
                        },
                    }
                },
            }
        )
    )
    monkeypatch.setenv("KAYDBOOKS_COMPOSIO_API_KEY", "synthetic-composio-" + "k" * 32)
    monkeypatch.setenv("KAYDBOOKS_COMPOSIO_COMPANY_A_GMAIL", "ca_synthetic_gmail")
    monkeypatch.setenv("KAYDBOOKS_COMPOSIO_COMPANY_A_CALENDAR", "ca_synthetic_calendar")
    return path


def test_write_requires_independent_approval_and_dispatches_once(setup, policy):
    bridge = setup[0]
    request = composio.prepare(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        policy,
        WRITE_TOOL,
        {"recipient_email": "synthetic@example.test", "body": "Synthetic status"},
        "message:synthetic:001",
    )
    assert request["state"] == "pending-approval"
    with pytest.raises(BridgeError, match="independent"):
        composio.approve(bridge, TOKENS["preparer-a"], "company-a", request["id"])
    approved = composio.approve(bridge, TOKENS["approver-a"], "company-a", request["id"])
    assert approved["state"] == "approved"
    calls = []

    def transport(tool, payload, api_key):
        calls.append((tool, payload, api_key))
        return {"successful": True, "log_id": "log_synthetic_001", "data": {"id": "one"}}

    result = composio.execute(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        policy,
        request["id"],
        transport=transport,
    )
    assert result["request"]["state"] == "completed"
    assert result["request"]["result_sha256"]
    assert calls[0][1]["version"] == "20260817_00"
    assert calls[0][1]["user_id"] == "kaydbooks-company-a"
    assert calls[0][1]["connected_account_id"] == "ca_synthetic_gmail"
    with pytest.raises(BridgeError, match="owned approved"):
        composio.execute(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            policy,
            request["id"],
            transport=transport,
        )
    assert len(calls) == 1
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]


def test_ambiguous_failure_is_unknown_and_cannot_retry(setup, policy):
    bridge = setup[0]
    request = composio.prepare(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        policy,
        READ_TOOL,
        {"calendar_id": "synthetic"},
        "calendar:synthetic:001",
    )
    assert request["state"] == "approved"
    attempts = []

    def timeout(*args):
        attempts.append(args)
        raise TimeoutError("synthetic ambiguous timeout")

    with pytest.raises(BridgeError, match="unknown"):
        composio.execute(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            policy,
            request["id"],
            transport=timeout,
        )
    assert (
        composio.status(bridge, TOKENS["preparer-a"], "company-a", request["id"])["state"]
        == "unknown"
    )
    with pytest.raises(BridgeError, match="owned approved"):
        composio.execute(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            policy,
            request["id"],
            transport=timeout,
        )
    assert len(attempts) == 1


def test_policy_idempotency_company_and_file_boundaries(setup, policy):
    bridge = setup[0]
    arguments = {"calendar_id": "synthetic"}
    first = composio.prepare(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        policy,
        READ_TOOL,
        arguments,
        "calendar:synthetic:002",
    )
    again = composio.prepare(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        policy,
        READ_TOOL,
        arguments,
        "calendar:synthetic:002",
    )
    assert again["id"] == first["id"]
    with pytest.raises(BridgeError, match="conflicts"):
        composio.prepare(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            policy,
            READ_TOOL,
            {"calendar_id": "other"},
            "calendar:synthetic:002",
        )
    with pytest.raises(BridgeError):
        composio.prepare(
            bridge,
            TOKENS["operator-b"],
            "company-b",
            policy,
            READ_TOOL,
            arguments,
            "calendar:synthetic:003",
        )
    with pytest.raises(BridgeError, match="file transfer"):
        composio.prepare(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            policy,
            READ_TOOL,
            {"attachment": r"C:\private\company.qbw"},
            "calendar:synthetic:004",
        )
