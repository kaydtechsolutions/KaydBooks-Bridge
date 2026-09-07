"""Fixed signed channel RPC over SSH. Not an agent or MCP approval tool.

The gateway host administrator is trusted. A model with unrestricted root shell
access on that host is outside this boundary; tool filtering alone is not a sandbox.
"""

import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

from . import hermes_batches as batches
from .config import BridgeError, strict_keys
from .service import Bridge
from .validation import canonical


def handle(bridge, settings, request, *, clock=time.time):
    strict_keys(request, {"company", "action", "parameters", "issued_at", "nonce", "signature"})
    secret = os.environ.get(settings["signing_secret_env"], "")
    unsigned = {k: v for k, v in request.items() if k != "signature"}
    expected = hmac.new(secret.encode(), canonical(unsigned).encode(), hashlib.sha256).hexdigest()
    if (
        len(secret) < 32
        or not isinstance(request["signature"], str)
        or not hmac.compare_digest(request["signature"], expected)
    ):
        raise BridgeError("channel authentication failed")
    stamp = request["issued_at"]
    if (
        type(stamp) not in (int, float)
        or not clock() - 120 <= stamp <= clock() + 30
        or not isinstance(request["nonce"], str)
        or not re.fullmatch("[a-f0-9]{32}", request["nonce"])
    ):
        raise BridgeError("fresh channel request required")
    company = request["company"]
    if company != settings["company"]:
        raise BridgeError("channel company binding differs")
    token = os.environ[settings["operator_token_env"]]
    reviewer = os.environ[settings["reviewer_token_env"]]
    args = request["parameters"]
    contracts = {
        "pending": set(),
        "status": {"batch_id"},
        "advance": {"batch_id"},
        "claim": {"batch_id", "kind"},
        "ack": {"batch_id", "kind", "provider_id"},
        "confirm": {
            "body",
            "event_id",
            "platform",
            "chat_id",
            "sender",
            "chat_type",
            "from_owner",
            "has_media",
        },
    }
    action = request["action"]
    if not isinstance(action, str) or action not in contracts:
        raise BridgeError("channel action unavailable")
    strict_keys(args, contracts[action])
    if action == "pending":
        _, actor, _, store = bridge._context(token, company, "read")
        with store.transaction() as db:
            batches.schema(db)
            if not store.verify_audit(db):
                raise BridgeError("audit integrity failed")
            return {
                "batch_ids": [
                    r[0]
                    for r in db.execute(
                        "SELECT b.id FROM hermes_batches b WHERE b.owner=? AND "
                        "(b.expires_at>? OR EXISTS(SELECT 1 FROM hermes_batch_confirmations c WHERE c.batch_id=b.id)) "
                        "AND NOT EXISTS(SELECT 1 FROM hermes_batch_delivery_receipts d WHERE d.batch_id=b.id AND d.kind='result') "
                        "ORDER BY b.created_at LIMIT 100",
                        (actor, bridge.clock()),
                    )
                ]
            }
    if action == "confirm":
        if (
            args["platform"] != "whatsapp"
            or args["chat_type"] != "dm"
            or args["chat_id"] != settings["chat_id"]
            or args["sender"] not in settings["sender_ids"]
            or args["from_owner"] is not False
            or args["has_media"] is not False
        ):
            raise BridgeError("trusted operator direct chat required")
        match = (
            re.fullmatch(r"/kb-confirm ([a-f0-9]{24}) ([a-f0-9]{16})", args["body"])
            if isinstance(args["body"], str)
            else None
        )
        if not match:
            raise BridgeError("exact confirmation reply required")
        return batches.confirm(
            bridge,
            token,
            company,
            match[1],
            match[2],
            reviewer_token=reviewer,
            event_id=args["event_id"],
            sender=args["sender"],
        )
    if action == "advance":
        return batches.advance(bridge, token, company, reviewer_token=reviewer, **args)
    if action == "status":
        return batches.status(bridge, token, company, **args)
    if not settings.get("allow_outbound", False):
        raise BridgeError("outbound channel messages are disabled")
    if args["batch_id"] not in settings.get("outbound_batch_ids", []):
        raise BridgeError("outbound batch is not authorized")
    if action == "claim":
        return batches.claim_delivery(bridge, token, company, **args)
    return batches.acknowledge_delivery(bridge, token, company, **args)


def main():
    from .deployment import load_secret_file

    try:
        if sys.argv[1:] == ["--clock"]:
            # Reached only through the administrator's authenticated SSH launcher.
            print(json.dumps({"ok": True, "result": {"server_time": time.time()}}))
            return 0
        if sys.argv[1:]:
            raise BridgeError("channel arguments unavailable")
        settings = json.loads(
            Path(os.environ["KAYDBOOKS_CHANNEL_CONFIG"]).read_text(encoding="utf-8")
        )
        load_secret_file(Path(os.environ["KAYDBOOKS_TOOL_SECRET_FILE"]))
        # Transport signing secret lives separately from Bridge credentials.
        load_secret_file(Path(settings["channel_secret_file"]))
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise BridgeError("channel request too large")
        request = json.loads(raw)
        result = handle(Bridge(os.environ["KAYDBOOKS_CONFIG"]), settings, request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except (BridgeError, KeyError, TypeError, ValueError, OSError):
        # Do not leak credentials/configuration in SSH stdout or tracebacks.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Channel request rejected; inspect private configuration and batch state.",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
