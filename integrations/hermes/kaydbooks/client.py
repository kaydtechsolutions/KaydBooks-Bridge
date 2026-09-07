"""Trusted gateway transport, with no model invocation or arbitrary shell input."""

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

_CLOCK_CACHE = {}


def _invoke(command, body=""):
    result = subprocess.run(
        command, input=body, text=True, capture_output=True, timeout=60, check=False
    )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Bridge channel transport did not return JSON") from exc
    if result.returncode or not value.get("ok"):
        raise RuntimeError(value.get("error", "Bridge channel request failed"))
    return value["result"]


def server_time(config):
    key = tuple(config["ssh_command"])
    cached = _CLOCK_CACHE.get(key)
    now = time.monotonic()
    if cached is None or now - cached[1] >= 60:
        value = _invoke([*config["ssh_command"], "-Clock"])["server_time"]
        if type(value) not in (int, float) or not 0 < value < 10**11:
            raise RuntimeError("Valid server clock required")
        cached = (value, time.monotonic())
        _CLOCK_CACHE[key] = cached
    return cached[0] + time.monotonic() - cached[1]


def settings():
    path = Path(
        os.environ.get("KAYDBOOKS_HERMES_CONFIG", "~/.hermes/kaydbooks/channel.json")
    ).expanduser()
    return json.loads(path.read_text())


def rpc(config, action, parameters):
    request = {
        "company": config["company"],
        "action": action,
        "parameters": parameters,
        "issued_at": server_time(config),
        "nonce": secrets.token_hex(16),
    }
    canonical = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    request["signature"] = hmac.new(
        config["signing_secret"].encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    # Only the administrator's fixed command is executable. Inputs travel over stdin.
    return _invoke(config["ssh_command"], json.dumps(request))


def confirmation_event(event, config):
    """Use native inbound body, never attachment text or a model's interpretation."""
    raw = getattr(event, "raw_message", None)
    source = getattr(event, "source", None)
    if not isinstance(raw, dict) or source is None:
        return None
    body = raw.get("body")
    if not isinstance(body, str) or not body.startswith("/kb-confirm"):
        return None
    platform = getattr(source.platform, "value", source.platform)
    if (
        platform != "whatsapp"
        or source.chat_type != "dm"
        or source.chat_id != config["chat_id"]
        or source.user_id not in config["sender_ids"]
        or raw.get("chatId") != source.chat_id
        or raw.get("senderId") != source.user_id
        or not raw.get("messageId")
        or raw.get("messageId") != event.message_id
        or raw.get("fromOwner")
        or getattr(event, "media_urls", None)
        or raw.get("hasMedia")
    ):
        raise ValueError("Confirmation requires the configured operator direct chat and plain text")
    return {
        "body": body,
        "event_id": event.message_id,
        "platform": platform,
        "chat_id": source.chat_id,
        "sender": source.user_id,
        "chat_type": source.chat_type,
        "from_owner": False,
        "has_media": False,
    }
