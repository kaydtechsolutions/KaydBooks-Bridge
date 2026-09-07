"""Corrected accounting payloads require new matching master evidence."""

# ruff: noqa: F811
import copy

import pytest

from kaydbooks_bridge import documents
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from test_bill_lookup import exact_case  # noqa: F401
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401
from test_supplier_credits import credit_case, queued_credit  # noqa: F401


def test_credit_revision_rejects_old_evidence_and_accepts_new_owned_check(queued_credit):
    bridge, token, job_id, session = queued_credit
    parent = bridge.status(token, "company-a", job_id)
    payload = copy.deepcopy(parent["payload"])
    payload["lines"][0]["amount"] = "5.00"
    kwargs = {
        "parent_id": job_id,
        "parent_fingerprint": parent["fingerprint"],
        "reason": "Correct original amount",
        "document_id": parent["source"]["original_values"]["document_id"],
        "idempotency_key": "credit-correction",
        "payload": payload,
        "confidence": {k: 1 for k in documents.fields(payload)},
    }
    old = {"transport": "direct-sdk", "connector": "connector-company-a", "id": "1234"}
    with pytest.raises(BridgeError):
        documents.revise(bridge, token, "company-a", **kwargs, master_evidence=old)
    assert bridge.status(token, "company-a", job_id)["state"] == "queued"
    discover(
        S.from_path(bridge.config_path),
        token,
        "connector-company-a",
        PASSWORD_A,
        "1235",
        supplier_credit_check=payload,
        exchange=session.read,
    )
    child = documents.revise(
        bridge, token, "company-a", **kwargs, master_evidence={**old, "id": "1235"}
    )
    assert (
        child["operation"] == "supplier-credit.create"
        and child["payload"]["lines"][0]["amount"] == "5.00"
    )
    assert child["master_evidence"]["reference"]["id"] == "1235"
    assert bridge.status(token, "company-a", job_id)["state"] == "superseded"
    assert session.writes == 0
