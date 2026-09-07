"""Deterministic optional worker; requires administrator-enabled outbound delivery."""

import argparse
import hashlib
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

from .client import rpc, settings

log = logging.getLogger(__name__)


def deliver(config, batch_id, kind):
    binding = [config.get("company"), config["chat_id"], batch_id, kind]
    filename = hashlib.sha256(json.dumps(binding).encode()).hexdigest() + ".json"
    folder = Path(
        config.get("receipt_directory", "~/.hermes/kaydbooks/delivery-receipts")
    ).expanduser()
    saved = folder / filename
    if saved.exists():
        receipt = json.loads(saved.read_text())
        if receipt["binding"] != binding:
            raise RuntimeError("Retained delivery binding differs")
        # Retry only the database acknowledgment, never the WhatsApp send.
        return rpc(
            config,
            "ack",
            {"batch_id": batch_id, "kind": kind, "provider_id": receipt["provider_id"]},
        )
    url = config.get("whatsapp_url", "http://127.0.0.1:3000")
    if url not in ("http://127.0.0.1:3000", "http://localhost:3000"):
        raise ValueError("Use the local Hermes WhatsApp bridge")
    with urllib.request.urlopen(url + "/health", timeout=10) as response:
        if json.load(response).get("status") != "connected":
            raise RuntimeError("WhatsApp is not connected; delivery has not been claimed")
    claim = rpc(config, "claim", {"batch_id": batch_id, "kind": kind})
    if not claim["claimed"]:
        return
    # Durable claim precedes network I/O. Unknown delivery is never retried automatically.
    req = urllib.request.Request(
        url + "/send",
        data=json.dumps(
            {
                "chatId": config["chat_id"],
                "message": claim["text"],
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        receipt = json.load(response)
    ids = receipt.get("messageIds") or [receipt.get("messageId")]
    if not receipt.get("success") or not ids or not all(isinstance(i, str) and i for i in ids):
        raise RuntimeError("WhatsApp delivery uncertain; inspect before any resend")
    provider_id = json.dumps(ids)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = saved.with_suffix(".tmp")
    temporary.write_text(json.dumps({"binding": binding, "provider_id": provider_id}))
    temporary.chmod(0o600)
    os.replace(temporary, saved)
    rpc(config, "ack", {"batch_id": batch_id, "kind": kind, "provider_id": provider_id})


def tick(config):
    pending = rpc(config, "pending", {})
    for batch_id in pending["batch_ids"]:
        try:
            state = rpc(config, "status", {"batch_id": batch_id})
            if not state["confirmed"]:
                if not state["expired"] and config.get("allow_outbound", False):
                    deliver(config, batch_id, "preview")
                continue
            if not (state["complete"] or state["held"]):
                state = rpc(config, "advance", {"batch_id": batch_id})
            if (state["complete"] or state["held"]) and config.get("allow_outbound", False):
                deliver(config, batch_id, "result")
        except Exception:
            log.exception("KaydBooks batch needs attention: %s", batch_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            tick(settings())
        except Exception:
            log.exception("KaydBooks worker needs attention")
            if args.once:
                raise
        if args.once:
            break
        time.sleep(15)


if __name__ == "__main__":
    main()
