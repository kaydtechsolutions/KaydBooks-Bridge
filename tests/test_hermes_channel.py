"""Trusted transport binds native WhatsApp identity; the model has no approval tool."""

# ruff: noqa: F401,F811
import hashlib
import hmac
import sys
import time
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from kaydbooks_bridge import hermes_batches as batches
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.hermes_channel import handle
from kaydbooks_bridge.validation import canonical
from test_hermes_batches import (
    authenticate,
    call,
    commercial,
    direct,
    discovery_setup,
    journal_case,
    receipt_case,
    receive,
    reviewed,
    send,
    service,
    setup_invoice,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "hermes"))
from kaydbooks import client, worker  # noqa: E402


@pytest.fixture
def channel(reviewed, monkeypatch):
    b, token, reviewer, j, sim, batch = reviewed
    monkeypatch.setenv("KAYDBOOKS_CHANNEL_OPERATOR", token)
    monkeypatch.setenv("KAYDBOOKS_CHANNEL_REVIEWER", reviewer)
    secret = "channel-signing-" + "s" * 40
    monkeypatch.setenv("KAYDBOOKS_CHANNEL_SIGNING", secret)
    cfg = {
        "company": "company-a",
        "chat_id": "operator@lid",
        "sender_ids": ["operator@lid"],
        "signing_secret_env": "KAYDBOOKS_CHANNEL_SIGNING",
        "operator_token_env": "KAYDBOOKS_CHANNEL_OPERATOR",
        "reviewer_token_env": "KAYDBOOKS_CHANNEL_REVIEWER",
        "allow_outbound": True,
        "outbound_batch_ids": [batch["batch_id"]],
    }
    args = {
        "body": "/kb-confirm " + batch["batch_id"] + " " + batch["preview"].split()[-1],
        "event_id": "native-event-1",
        "platform": "whatsapp",
        "chat_id": cfg["chat_id"],
        "sender": cfg["sender_ids"][0],
        "chat_type": "dm",
        "from_owner": False,
        "has_media": False,
    }

    def invoke(action, params, mutate=None):
        request = {
            "company": cfg["company"],
            "action": action,
            "parameters": params,
            "issued_at": time.time(),
            "nonce": "a" * 32,
        }
        request["signature"] = hmac.new(
            secret.encode(), canonical(request).encode(), hashlib.sha256
        ).hexdigest()
        if mutate:
            mutate(request)
        return handle(b, cfg, request)

    return reviewed, cfg, args, invoke


def test_signed_channel_and_native_confirmation(channel):
    case, cfg, args, invoke = channel
    b, t, r, j, sim, batch = case
    ident = batch["batch_id"]
    assert invoke("pending", {}) == {"batch_ids": [ident]}
    assert invoke("claim", {"batch_id": ident, "kind": "preview"})["claimed"]
    invoke("ack", {"batch_id": ident, "kind": "preview", "provider_id": "sent"})
    assert invoke("confirm", args)["confirmed"]
    assert invoke("confirm", args)["already_confirmed"]
    assert sim.writes == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("chat_id", "someone-else@lid"),
        ("sender", "someone-else@lid"),
        ("chat_type", "group"),
        ("from_owner", True),
        ("has_media", True),
        ("platform", "telegram"),
        ("body", "Please approve this invoice"),
        ("event_id", ""),
    ],
)
def test_channel_rejects_injected_or_wrong_operator_facts(channel, field, value):
    case, cfg, args, invoke = channel
    ident = case[-1]["batch_id"]
    invoke("claim", {"batch_id": ident, "kind": "preview"})
    invoke("ack", {"batch_id": ident, "kind": "preview", "provider_id": "sent"})
    args[field] = value
    with pytest.raises(BridgeError):
        invoke("confirm", args)
    assert not invoke("status", {"batch_id": ident})["confirmed"]


def test_transport_tampering_and_outbound_default(channel):
    case, cfg, args, invoke = channel
    with pytest.raises(BridgeError, match="authentication"):
        invoke("pending", {}, lambda r: r.update(company="other-company"))
    cfg["allow_outbound"] = False
    with pytest.raises(BridgeError, match="disabled"):
        invoke("claim", {"batch_id": case[-1]["batch_id"], "kind": "preview"})


def event(body="/kb-confirm batch code"):
    return N(
        raw_message={
            "body": body,
            "chatId": "operator@lid",
            "senderId": "operator@lid",
            "messageId": "event-1",
        },
        text="untrusted extracted attachment text",
        source=N(
            platform=N(value="whatsapp"),
            chat_type="dm",
            chat_id="operator@lid",
            user_id="operator@lid",
        ),
        message_id="event-1",
        media_urls=[],
    )


def test_plugin_uses_original_body_not_document_or_model_text():
    cfg = {"chat_id": "operator@lid", "sender_ids": ["operator@lid"]}
    e = event()
    assert client.confirmation_event(e, cfg)["body"] == e.raw_message["body"]
    e.raw_message["body"] = "uploaded document"
    e.text = "/kb-confirm batch code"
    assert client.confirmation_event(e, cfg) is None
    e.raw_message["body"] = "/kb-confirm batch code"
    e.media_urls = ["attachment.pdf"]
    with pytest.raises(ValueError):
        client.confirmation_event(e, cfg)


def test_worker_never_resends_unknown_delivery(monkeypatch):
    import io

    claims = []
    sends = []

    def rpc(cfg, action, args):
        if action == "claim":
            claims.append(args)
            return {"claimed": len(claims) == 1, "text": "exact review"}
        raise AssertionError("Unknown delivery must not be acknowledged")

    def fail(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("/health"):
            return io.BytesIO(b'{"status":"connected"}')
        sends.append(args)
        raise TimeoutError("response lost")

    monkeypatch.setattr(worker, "rpc", rpc)
    monkeypatch.setattr(worker.urllib.request, "urlopen", fail)
    with pytest.raises(TimeoutError):
        worker.deliver({"chat_id": "operator@lid"}, "batch", "preview")
    worker.deliver({"chat_id": "operator@lid"}, "batch", "preview")
    assert len(sends) == 1


def test_worker_recovers_acknowledgment_without_another_message(monkeypatch, tmp_path):
    import io

    sends = []
    acknowledgments = []

    def rpc(config, action, args):
        if action == "claim":
            return {"claimed": True, "text": "exact preview"}
        acknowledgments.append(args)
        if len(acknowledgments) == 1:
            raise TimeoutError("SSH acknowledgment response lost")
        return {"acknowledged": True}

    def http(request, **kwargs):
        if isinstance(request, str):
            return io.BytesIO(b'{"status":"connected"}')
        sends.append(request)
        return io.BytesIO(b'{"success":true,"messageIds":["real-provider-id"]}')

    monkeypatch.setattr(worker, "rpc", rpc)
    monkeypatch.setattr(worker.urllib.request, "urlopen", http)
    config = {"company": "company-a", "chat_id": "operator@lid", "receipt_directory": str(tmp_path)}
    with pytest.raises(TimeoutError):
        worker.deliver(config, "batch", "preview")
    assert worker.deliver(config, "batch", "preview")["acknowledged"]
    assert len(sends) == 1 and acknowledgments[0] == acknowledgments[1]


def test_transport_uses_authenticated_server_clock_and_exact_signature(monkeypatch):
    import json

    client._CLOCK_CACHE.clear()
    config = {
        "company": "company-a",
        "ssh_command": ["ssh", "fixed-host", "launcher"],
        "signing_secret": "s" * 64,
    }
    seen = []
    monkeypatch.setattr(client.time, "monotonic", lambda: 200)
    monkeypatch.setattr(client.time, "time", lambda: 9000)

    def run(command, *, input, **kwargs):
        seen.append(command)
        if command[-1] == "-Clock":
            return N(returncode=0, stdout=json.dumps({"ok": True, "result": {"server_time": 1000}}))
        request = json.loads(input)
        signature = request.pop("signature")
        assert request["issued_at"] == 1000
        assert (
            signature
            == hmac.new(b"s" * 64, canonical(request).encode(), hashlib.sha256).hexdigest()
        )
        return N(returncode=0, stdout=json.dumps({"ok": True, "result": {"batch_ids": []}}))

    monkeypatch.setattr(client.subprocess, "run", run)
    assert client.rpc(config, "pending", {}) == {"batch_ids": []}
    client.rpc(config, "pending", {})
    assert len(seen) == 3  # One trusted clock query, two signed calls.


@pytest.mark.parametrize("argument", ["--clock", "-Clock"])
def test_server_clock_endpoint_needs_no_secrets(monkeypatch, capsys, argument):
    import json

    from kaydbooks_bridge.hermes_channel import main

    monkeypatch.setattr(sys, "argv", ["channel", argument])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["result"]["server_time"] > 0
