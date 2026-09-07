"""Verified history selection cannot substitute for fresh accounting checks."""
# ruff: noqa: F811

import json

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.transaction_choices import search
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_receipt_lifecycle import read_saved, receipt_exchange, saved_job  # noqa: F401


def choose(case, **changes):
    bridge, token, _, envelope, _ = case
    args = dict(
        connector_id="connector-company-a",
        operation="customer-credit.create",
        party_id=envelope["payload"]["customer_id"],
        kind="invoice",
        search="",
        cursor="",
    )
    args.update(changes)
    return search(bridge, token, "company-a", **args)


def verified(case):
    bridge, token, job, _, reference = case
    read_saved(case, exchange=receipt_exchange())
    return bridge.attach_receipt(token, "company-a", job, reference)


def test_saved_history_survives_restart_and_is_not_balance_evidence(saved_job):
    assert choose(saved_job)["choices"] == []  # Validated draft is not a selectable original.
    verified(saved_job)
    result = choose(saved_job)
    assert result["requires_fresh_check"] and result["next_cursor"] is None
    assert result["choices"][0]["original_amount"] == "10.00"
    assert result["choices"][0]["txn_id"] == "saved-id"
    assert "balance" not in result["choices"][0]
    restarted = (Bridge(saved_job[0].config_path), *saved_job[1:])
    assert choose(restarted)["choices"] == result["choices"]
    assert choose(saved_job, search="sYn-cH")["choices"] == result["choices"]
    assert choose(saved_job, search="saved-id")["choices"] == result["choices"]
    assert choose(saved_job, search="%' OR 1=1--")["choices"] == []
    assert choose(saved_job, cursor="1")["choices"] == []
    assert saved_job[0].status(saved_job[1], "company-a", saved_job[2])["attempt"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"cursor": "0"},
        {"cursor": "-1"},
        {"cursor": "9223372036854775808"},
        {"cursor": 1},
        {"search": "x" * 81},
        {"search": "\n"},
        {"search": []},
        {"operation": "invoice.create"},
        {"kind": "bill"},
        {"party_id": "missing"},
        {"connector_id": "connector-company-b"},
    ],
)
def test_closed_selector_and_bounds(saved_job, change):
    with pytest.raises(BridgeError):
        choose(saved_job, **change)


def test_same_alias_remapped_to_another_native_customer_is_not_a_match(saved_job):
    verified(saved_job)
    path = saved_job[0].config_path
    raw = json.loads(path.read_text())
    alias = saved_job[3]["payload"]["customer_id"]
    raw["companies"]["company-a"]["invoice_masters"]["customers"][alias] = "other-native-customer"
    path.write_text(json.dumps(raw))
    assert choose(saved_job)["choices"] == []


def test_revocation_and_company_boundary_apply_to_each_search(saved_job):
    verified(saved_job)
    bridge, token, *_ = saved_job
    with pytest.raises(BridgeError):
        search(
            bridge,
            token,
            "company-b",
            "connector-company-b",
            "customer-credit.create",
            "customer-a",
            "invoice",
            "",
        )
    raw = json.loads(bridge.config_path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = []
    bridge.config_path.write_text(json.dumps(raw))
    with pytest.raises(BridgeError):
        choose(saved_job)


def test_missing_retained_response_is_not_silently_trusted(saved_job, monkeypatch):
    verified(saved_job)
    from kaydbooks_bridge.store import Store

    original = Store.job

    def changed(db, job_id):
        job = original(db, job_id)
        job["transaction_receipt"]["response_sha256"] = "0" * 64
        return job

    monkeypatch.setattr(Store, "job", staticmethod(changed))
    with pytest.raises(BridgeError, match="missing or changed"):
        choose(saved_job)


def test_qbwc_verified_history_is_selectable(saved_job, tmp_path):
    from test_qbwc_receipts import complete

    _, reference = complete(saved_job, tmp_path)
    bridge, token, job, *_ = saved_job
    bridge.attach_receipt(token, "company-a", job, reference)
    assert choose(saved_job)["choices"][0]["txn_id"] == "saved-id"
