"""Authenticated local CLI with an explicit controlled-sample posting command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .capabilities import inventory
from .config import BridgeError, Config
from .service import Bridge


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KaydBooks Bridge (production posting disabled)")
    parser.add_argument("--config", default=os.environ.get("KAYDBOOKS_CONFIG"))
    parser.add_argument("--company", help="explicit company ID; never inferred")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    commands.add_parser("check-config")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("input", type=Path, help="structured synthetic envelope JSON")
    for action in (
        "validate",
        "approve",
        "submit",
        "reconcile",
        "post-sample",
        "reconcile-sample",
        "post-sample-bill",
        "reconcile-sample-bill",
        "post-sample-credit",
        "reconcile-sample-credit",
        "post-sample-supplier-payment",
        "reconcile-sample-supplier-payment",
        "post-sample-payment",
        "reconcile-sample-payment",
    ):
        commands.add_parser(action).add_argument("job_id")
    commands.add_parser("status").add_argument("job_id", nargs="?")
    commands.add_parser("preview").add_argument("job_id")
    review = commands.add_parser("review-source")
    review.add_argument("job_id")
    review.add_argument("review", type=Path, help="fingerprint and confirmed_values JSON")
    for action in ("attach-receipt", "verify-receipt"):
        receipt = commands.add_parser(action)
        receipt.add_argument("job_id")
        receipt.add_argument(
            "reference", type=Path, help="private durable receipt evidence reference JSON"
        )
    for action in ("simulate", "recover", "pause", "resume", "audit"):
        commands.add_parser(action)
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            result = inventory()
        elif args.command == "check-config":
            if not args.config:
                raise BridgeError("private config path required")
            config = Config.load(args.config)
            config.authenticate(os.environ.get("KAYDBOOKS_TOKEN", ""))
            result = {"valid": True, "mode": "simulation", "live_posting": False}
        else:
            if not args.config or not args.company:
                raise BridgeError("private config path and explicit company required")
            bridge = Bridge(args.config)
            token = os.environ.get("KAYDBOOKS_TOKEN", "")
            if args.command == "prepare":
                if args.input.stat().st_size > 131072:
                    raise BridgeError("input too large")
                envelope = json.loads(args.input.read_text(encoding="utf-8"))
                # A local invocation always attributes itself as CLI.
                envelope["surface"] = "cli"
                result = bridge.prepare(token, args.company, envelope)
            elif args.command in {"validate", "approve", "submit"}:
                result = bridge.action(token, args.company, args.job_id, args.command)
            elif args.command == "reconcile":
                result = bridge.reconcile(token, args.company, args.job_id)
            elif args.command in (
                "post-sample",
                "reconcile-sample",
                "post-sample-bill",
                "reconcile-sample-bill",
                "post-sample-credit",
                "reconcile-sample-credit",
                "post-sample-supplier-payment",
                "reconcile-sample-supplier-payment",
                "post-sample-payment",
                "reconcile-sample-payment",
            ):
                from .sample_posting import post, reconcile

                if args.command.endswith("-bill"):
                    from .sample_bill_posting import post, reconcile
                if args.command.endswith("-credit"):
                    from .sample_credit_posting import post, reconcile
                elif args.command.endswith("-supplier-payment"):
                    from .sample_supplier_payment_posting import post, reconcile
                elif args.command.endswith("-payment"):
                    from .sample_payment_posting import post, reconcile

                result = (post if args.command.startswith("post-") else reconcile)(
                    bridge, token, args.company, args.job_id
                )
            elif args.command == "status":
                result = bridge.status(token, args.company, args.job_id)
            elif args.command == "preview":
                result = bridge.preview(token, args.company, args.job_id)
            elif args.command == "review-source":
                from .config import strict_keys
                from .source_review import review

                if args.review.stat().st_size > 65536:
                    raise BridgeError("review too large")
                values = json.loads(args.review.read_text(encoding="utf-8"))
                strict_keys(values, {"fingerprint", "confirmed_values"})
                result = review(bridge, token, args.company, args.job_id, **values)
            elif args.command in ("attach-receipt", "verify-receipt"):
                if args.reference.stat().st_size > 4096:
                    raise BridgeError("receipt reference too large")
                result = getattr(bridge, args.command.replace("-", "_"))(
                    token,
                    args.company,
                    args.job_id,
                    json.loads(args.reference.read_text(encoding="utf-8")),
                )
            elif args.command in {"pause", "resume"}:
                result = bridge.pause(token, args.company, args.command == "pause")
            else:
                result = getattr(bridge, args.command)(token, args.company)
        print(json.dumps(result, indent=2))
        return 0
    except BridgeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        # Do not echo private paths, JSON content or credentials into diagnostics.
        print(json.dumps({"error": "invalid or inaccessible private input"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
