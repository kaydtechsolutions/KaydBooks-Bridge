"""Bill lifecycle tests are synthetic; no native bill capability is claimed."""

import copy
import json
import sqlite3
from dataclasses import asdict

import pytest

from kaydbooks_bridge.config import BridgeError, Config, company_policy_context
from kaydbooks_bridge.sample_posting import post
from kaydbooks_bridge.service import Bridge
from kaydbooks_bridge.simulation import SyntheticLedger
from kaydbooks_bridge.store import Store
from test_bridge import TOKENS, setup  # noqa: F401


@pytest.fixture
def bill_setup(setup):  # noqa: F811
    bridge, path, config, invoice = setup
    config["companies"]["company-a"]["bill_masters"] = {
        "vendors": {"vendor-a": "V-A", "vendor-b": "V-B", "vendor-alias": "V-A"},
        "payable": "AP-A",
        "expenses": {"office": "E-A", "travel": "E-B"},
    }
    config["principals"]["preparer-a"]["companies"]["company-a"].append("post-sample")
    path.write_text(json.dumps(config))
    envelope = copy.deepcopy(invoice)
    envelope.update(operation="bill.create", idempotency_key="bill-a")
    envelope["source"]["reference"] = "bill-source-a"
    envelope["payload"] = {
        "vendor_id": "vendor-a",
        "txn_date": "2026-09-06",
        "due_date": "2026-10-06",
        "ref_number": "BILL-001",
        "currency": "USD",
        "lines": [{"expense_id": "office", "amount": "10.00"}],
    }
    return bridge, path, config, envelope, invoice


def prepare(case):
    return case[0].prepare(TOKENS["preparer-a"], "company-a", case[3])


def queue(case):
    bridge = case[0]
    job = prepare(case)
    bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "validate")
    bridge.action(TOKENS["approver-a"], "company-a", job["id"], "approve")
    return bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "submit")


def test_bill_lifecycle_and_invoice_history_survive_restart(bill_setup):
    bridge, path, _, _, invoice = bill_setup
    original = bridge.prepare(TOKENS["preparer-a"], "company-a", invoice)
    before = bridge.status(TOKENS["preparer-a"], "company-a", original["id"])
    job = queue(bill_setup)
    result = bridge.simulate(TOKENS["operator-a"], "company-a")
    assert result["state"] == "verified" and result["txn_id"].startswith("sim-bill-")
    assert "transaction_receipt" not in result
    restarted = Bridge(path)
    assert restarted.status(TOKENS["preparer-a"], "company-a", original["id"]) == before
    assert prepare(bill_setup)["id"] == job["id"]
    assert restarted.audit(TOKENS["operator-a"], "company-a")["valid"]


def test_vendor_reference_is_scoped_and_alias_cannot_duplicate(bill_setup):
    first = prepare(bill_setup)
    envelope = bill_setup[3]
    envelope["idempotency_key"] = "bill-b"
    envelope["source"]["reference"] = "bill-source-b"
    envelope["payload"]["vendor_id"] = "vendor-b"
    assert prepare(bill_setup)["id"] != first["id"]
    envelope["payload"]["vendor_id"] = "vendor-alias"
    envelope["idempotency_key"] = "bill-alias"
    envelope["source"]["reference"] = "bill-source-alias"
    with pytest.raises(BridgeError, match="conflicts"):
        prepare(bill_setup)


@pytest.mark.parametrize(
    "key,value",
    [
        ("vendor_id", "unknown"),
        ("currency", "EUR"),
        ("due_date", "2026-01-01"),
        ("txn_date", "2026-02-30"),
        ("tax_amount", "1.00"),
        ("lines", []),
        ("lines", [{"expense_id": "unknown", "amount": "1.00"}]),
        ("lines", [{"expense_id": "office", "amount": "-1.00"}]),
        ("lines", [{"expense_id": "office", "amount": "1.001"}]),
        ("lines", [{"expense_id": "office", "amount": "999999.00"}]),
        ("lines", [{"expense_id": "office", "amount": "1.00", "raw_xml": "unsafe"}]),
    ],
)
def test_bill_rejects_unsupported_or_invalid_payloads(bill_setup, key, value):
    bill_setup[3]["payload"][key] = value
    with pytest.raises(BridgeError):
        prepare(bill_setup)


def test_bill_requires_approval_and_native_invoice_adapter_refuses_it(bill_setup):
    bridge = bill_setup[0]
    job = prepare(bill_setup)
    bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "validate")
    preview = bridge.preview(TOKENS["preparer-a"], "company-a", job["id"])
    assert preview["total"] == "10.00" and not preview["master_evidence_verified"]
    with pytest.raises(BridgeError, match="approval required"):
        bridge.action(TOKENS["preparer-a"], "company-a", job["id"], "submit")
    with pytest.raises(BridgeError, match="native posting is unavailable"):
        post(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            job["id"],
            exchange=lambda *_: pytest.fail("native write"),
        )


@pytest.mark.parametrize("mapping", ["vendors", "expenses"])
def test_bill_mapping_cannot_retarget_existing_job(bill_setup, mapping):
    job = queue(bill_setup)
    bridge, path, config, _, _ = bill_setup
    config["companies"]["company-a"]["bill_masters"][mapping][
        "vendor-a" if mapping == "vendors" else "office"
    ] = "CHANGED"
    path.write_text(json.dumps(config))
    with pytest.raises(BridgeError, match="mapping changed"):
        bridge.simulate(TOKENS["operator-a"], "company-a")
    assert bridge.status(TOKENS["preparer-a"], "company-a", job["id"])["state"] == "queued"


def test_bill_binding_and_evidence_types_are_immutable(bill_setup):
    job = prepare(bill_setup)
    config = Config.load(bill_setup[1])
    store = Store(config.root, "company-a")
    with store.transaction() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE bill_policy_bindings SET context='{}' WHERE job_id=?", (job["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM bill_policy_bindings WHERE job_id=?", (job["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="invoice"):
            db.execute(
                "INSERT INTO invoice_evidence_links(job_id,evidence) VALUES (?,?)",
                (job["id"], "{}"),
            )


def test_lost_bill_response_recovers_without_duplicate(bill_setup, monkeypatch):
    job = queue(bill_setup)
    bridge = bill_setup[0]
    original = SyntheticLedger.write

    def lost(self, payload):
        original(self, payload)
        raise RuntimeError("response lost")

    monkeypatch.setattr(SyntheticLedger, "write", lost)
    assert bridge.simulate(TOKENS["operator-a"], "company-a")["state"] == "unknown"
    monkeypatch.setattr(SyntheticLedger, "write", lambda *_: pytest.fail("must not resend"))
    assert (
        Bridge(bill_setup[1]).reconcile(TOKENS["operator-a"], "company-a", job["id"])["state"]
        == "verified"
    )
    assert bridge.simulate(TOKENS["operator-a"], "company-a") is None


def test_native_invoice_evidence_cannot_be_passed_as_bill_evidence(bill_setup):
    bill_setup[3]["master_evidence"] = {
        "transport": "direct-sdk",
        "id": "1",
        "connector": "connector-company-a",
    }
    with pytest.raises(BridgeError, match="verified owned exact bill master evidence required"):
        prepare(bill_setup)


def test_legacy_policy_hash_shape_is_preserved(setup):  # noqa: F811
    policy = Config.load(setup[1]).companies["company-a"]
    old_shape = asdict(policy)
    old_shape.pop("bill_masters")
    old_shape.pop("sample_bill_posting")
    old_shape.pop("payment_masters")
    old_shape.pop("sample_payment_posting")
    old_shape.pop("supplier_payment_masters")
    old_shape.pop("sample_supplier_payment_posting")
    old_shape.pop("sample_credit_posting")
    old_shape.pop("sample_refund_posting")
    old_shape.pop("sample_application_posting")
    assert company_policy_context(policy) == old_shape
