"""Local workflow/report operations; no scheduler daemon or external deliveries."""

import argparse
import json
import os
from pathlib import Path

from . import reports, workflows
from .config import BridgeError, strict_keys
from .service import Bridge


def main(argv=None):
    parser = argparse.ArgumentParser(description="KaydBooks local workflows and receipt reports")
    parser.add_argument("--config", default=os.environ.get("KAYDBOOKS_CONFIG"))
    parser.add_argument("--company", required=True)
    parser.add_argument(
        "action",
        choices=[
            "board",
            "schedule",
            "cancel",
            "tick",
            "remember",
            "memory",
            "delegate",
            "report",
            "export",
        ],
    )
    parser.add_argument("input", nargs="?", type=Path)
    args = parser.parse_args(argv)
    contracts = {
        "board": set(),
        "tick": set(),
        "memory": set(),
        "schedule": {
            "schedule_id",
            "timezone",
            "first_run",
            "interval_seconds",
            "max_runs",
            "dependencies",
        },
        "cancel": {"schedule_id"},
        "remember": {"name", "value", "expires_at", "provenance", "expected_version"},
        "delegate": {"job_id", "assignee"},
        "report": {"date_from", "date_to"},
        "export": {"date_from", "date_to", "destination"},
    }
    try:
        if not args.config:
            raise BridgeError("private config required")
        if args.input and args.input.stat().st_size > 65536:
            raise BridgeError("workflow input too large")
        values = json.loads(args.input.read_text(encoding="utf-8")) if args.input else {}
        strict_keys(values, contracts[args.action])
        method = (
            reports.register
            if args.action == "report"
            else reports.export
            if args.action == "export"
            else getattr(workflows, args.action)
        )
        result = method(
            Bridge(args.config), os.environ.get("KAYDBOOKS_TOKEN", ""), args.company, **values
        )
        print(json.dumps(result, indent=2))
        return 0
    except (BridgeError, OSError, TypeError, ValueError, KeyError):
        print(json.dumps({"error": "workflow request rejected; inspect company audit"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
