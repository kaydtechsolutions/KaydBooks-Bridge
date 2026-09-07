"""Review output cannot bypass ownership, policy, evidence freshness or job state."""

import json
from pathlib import Path

import pytest

from kaydbooks_bridge.cli import main
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.direct_sdk import discover
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.validation import digest
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_qbwc_discovery import PASSWORD_A, discovery_setup  # noqa: F401


@pytest.fixture
def review_job(commercial):  # noqa: F811
    path, token, payload = commercial
    svc = DurableQBWCDiscoveryService.from_path(path)
    discover(
        svc,
        token,
        "connector-company-a",
        PASSWORD_A,
        "971",
        invoice_check=payload,
        exchange=lambda request, destination: destination.write_text(response(request)),
    )
    envelope = json.loads(
        (Path(__file__).parents[1] / "examples/synthetic-invoice.json").read_text()
    )
    envelope.update(
        payload=payload,
        master_evidence={
            "transport": "direct-sdk",
            "connector": "connector-company-a",
            "id": "971",
        },
    )
    bridge = Bridge(path)
    job = bridge.prepare(token, "company-a", envelope)
    return bridge, token, job


def test_review_is_deterministic_audited_and_has_no_state_transition(review_job):
    bridge, token, job = review_job
    validated = bridge.action(token, "company-a", job["id"], "validate")
    review = bridge.preview(token, "company-a", job["id"])
    assert review["preview_sha256"] == digest(
        {key: value for key, value in review.items() if key != "preview_sha256"}
    )
    assert review["lines"][0]["master"]["list_id"] == "service-id"
    assert review["customer"]["list_id"] == "customer-id"
    assert review["receivable_account_id"] == "ar-id"
    assert "original_values" not in review["source"]
    assert bridge.status(token, "company-a", job["id"]) == validated
    assert Bridge(bridge.config_path).preview(token, "company-a", job["id"]) == review
    audit = bridge.audit(token, "company-a")
    assert audit["valid"]
    assert [e["data"] for e in audit["events"] if e["event"] == "invoice_previewed"] == [
        {"preview_sha256": review["preview_sha256"]}
    ] * 2


@pytest.mark.parametrize("case", ["draft", "expired", "permission", "policy", "binding", "owner"])
def test_review_rejects_unqualified_access_or_evidence(review_job, case, monkeypatch):
    bridge, token, job = review_job
    if case != "draft":
        bridge.action(token, "company-a", job["id"], "validate")
    raw = json.loads(Path(bridge.config_path).read_text())
    actor = next(iter(raw["principals"]))
    if case == "expired":
        expiry = job["master_evidence"]["observed_at"] + 900
        bridge = Bridge(bridge.config_path, clock=lambda: expiry)
    elif case == "permission":
        raw["principals"][actor]["companies"]["company-a"].remove("validate")
    elif case == "policy":
        raw["companies"]["company-a"]["invoice_masters"]["commercial"]["tax_rate"] = "11"
    elif case == "binding":
        raw["connectors"]["connector-company-a"]["identity_sha256"] = "f" * 64
    elif case == "owner":
        raw["principals"]["other-reviewer"] = {
            **raw["principals"][actor],
            "token_env": "KAYDBOOKS_OTHER_REVIEW_TOKEN",
        }
        token = "other-reviewer-synthetic-token-xxxxxxxxxxxxxxxx"
        monkeypatch.setenv("KAYDBOOKS_OTHER_REVIEW_TOKEN", token)
    Path(bridge.config_path).write_text(json.dumps(raw))
    with pytest.raises(BridgeError, match="owned validated" if case == "owner" else None):
        bridge.preview(token, "company-a", job["id"])
    audit = bridge.audit(token, "company-a")
    assert not any(e["event"] == "invoice_previewed" for e in audit["events"])


def test_cli_preview_uses_same_authenticated_gate(review_job, monkeypatch, capsys):
    bridge, token, job = review_job
    bridge.action(token, "company-a", job["id"], "validate")
    monkeypatch.setenv("KAYDBOOKS_TOKEN", token)
    assert (
        main(["--config", str(bridge.config_path), "--company", "company-a", "preview", job["id"]])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == "11.00" and result["live_posting"] is False
