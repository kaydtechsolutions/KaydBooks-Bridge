"""Account-role checks over private, verified exact lookup evidence; no posting."""

import argparse
import json
import os
import re
import time

from .account_lookup import append_query, validate_list_id, validate_response
from .config import BridgeError
from .validation import digest

RULES = {
    ("invoice.create", "receivable"): ("invoice_receivable", "AccountsReceivable"),
    ("master.change", "income"): ("master_income", "Income"),
    ("master.change", "expense"): ("master_expense", "Expense"),
    ("master.change", "cogs"): ("master_cogs", "CostOfGoodsSold"),
    ("master.change", "asset"): ("master_asset", "OtherCurrentAsset"),
}


def validate_roles(value):
    if not isinstance(value, dict) or value.keys() - {rule[0] for rule in RULES.values()}:
        raise BridgeError("unsupported account role configuration")
    for list_id in value.values():
        if list_id is None:
            raise BridgeError("account role requires an explicit ListID")
        validate_list_id(list_id)
    return dict(value)


def check_role(service, token, connector_id, operation, role, transport, evidence_id):
    actor = service.config.authenticate(token)
    connector = service.config.connectors.get(connector_id)
    if connector is None:
        raise BridgeError("unknown connector")
    company = service.config.authorize(actor, connector.company, "validate")
    service.config.authorize(actor, connector.company, "read")
    rule = RULES.get((operation, role))
    if rule is None:
        raise BridgeError("unsupported operation or account role")
    roles = validate_roles(company.account_roles)
    target = roles.get(rule[0])
    if target is None:
        raise BridgeError("account role is not configured")
    store = service._stores[connector.company]
    with store.transaction() as db:
        if transport == "qbwc":
            row = db.execute(
                "SELECT s.*,j.actor,j.connector,j.list_id FROM qbwc_account_jobs j "
                "JOIN qbwc_sessions s ON s.ticket=j.ticket WHERE j.id=?",
                (evidence_id,),
            ).fetchone()
            if (
                row is None
                or row["state"] not in ("verified", "closed")
                or row["response_result"] != 100
                or row["last_error"]
                or row["list_id"] != target
            ):
                raise BridgeError("verified exact account evidence required")
            payload, correlation = row["response_xml"], row["correlation"]
            context = row
        elif transport == "direct-sdk":
            if not isinstance(evidence_id, str) or not re.fullmatch(
                r"[1-9][0-9]{0,15}", evidence_id
            ):
                raise BridgeError("invalid SDK evidence ID")
            row = db.execute("SELECT * FROM sdk_discovery WHERE id=?", (evidence_id,)).fetchone()
            expected = append_query(
                service._discovery_request(evidence_id, "17.0"), evidence_id, list_id=target
            )
            if row is None or row["state"] != "verified" or row["request"] != expected:
                raise BridgeError("verified exact account evidence required")
            payload, correlation = row["response"], evidence_id
            context = {"correlation": correlation, "country": "US", "qbxml_version": "17.0"}
        else:
            raise BridgeError("unsupported evidence transport")
        if row["actor"] != actor or row["connector"] != connector_id:
            raise BridgeError("account evidence ownership mismatch")
        discovery, records = validate_response(payload, correlation, target)
        service._verify_discovery_response(discovery, context, connector)
        if records[0]["AccountType"] != rule[1]:
            raise BridgeError("account type is incompatible with operation role")
        result = {
            "operation": operation,
            "role": role,
            "transport": transport,
            "evidence_id": evidence_id,
            "state": "role-matched",
            "scope": "saved-evidence-only",
            "policy_sha256": digest(roles),
            "response_sha256": digest(payload),
            "live_posting": False,
        }
        store.event(db, time.time(), actor, None, "account_role_checked", result)
        return result


def main(argv=None):
    from .deployment import load_secret_file
    from .qbwc import DurableQBWCDiscoveryService

    parser = argparse.ArgumentParser(
        description="Check a configured account role against saved exact evidence"
    )
    for name in (
        "config",
        "credentials",
        "principal",
        "connector",
        "operation",
        "role",
        "transport",
        "evidence-id",
    ):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        load_secret_file(args.credentials)
        service = DurableQBWCDiscoveryService.from_path(args.config)
        principal = service.config.principals[args.principal]
        print(
            json.dumps(
                check_role(
                    service,
                    os.environ.get(principal["token_env"], ""),
                    args.connector,
                    args.operation,
                    args.role,
                    args.transport,
                    args.evidence_id,
                )
            )
        )
        return 0
    except (BridgeError, OSError, ValueError, KeyError):
        print(
            json.dumps(
                {"error": "account role check rejected; inspect private policy and evidence"}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
