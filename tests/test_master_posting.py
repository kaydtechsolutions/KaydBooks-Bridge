"""Sample master writes use shared approval/state/audit and independent lookup."""

# ruff: noqa: F811
import copy
import hashlib
import json
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge import master_checks as checks
from kaydbooks_bridge import master_posting as posting
from kaydbooks_bridge import master_records as masters
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.service import Bridge
from test_direct_sdk import direct  # noqa: F401
from test_master_records import (  # noqa: F401
    existing,
    master_case,
    native_response,
    proposal,
    transport,
)
from test_qbwc_discovery import discovery_setup  # noqa: F401


class Native:
    def __init__(self, kind, before=None, lost=False):
        self.kind, self.before, self.lost = kind, before, lost
        self.saved = None
        self.writes = 0

    def entities(self):
        return (
            {
                masters.KINDS[self.kind]: [self.saved],
                ("Entity" if self.kind in ("customer", "supplier") else "Item"): [self.saved],
            }
            if self.saved
            else {}
        )

    def read(self, request, folder, callback):
        return transport(self.entities())(request, folder, callback)

    def __call__(self, request, write, folder, approve):
        folder.mkdir()
        text = native_response(request, self.entities())
        (folder / "preflight.response.xml").write_text(text)
        try:
            if self.before:
                self.before()
            assert approve(text)
            (folder / "write-intent.txt").write_text(hashlib.sha256(write.encode()).hexdigest())
            rq = ET.fromstring(write)[0][0]
            node = rq[0]
            self.saved = existing(self.kind) if self.saved is None else copy.deepcopy(self.saved)
            self.writes += 1
            self.saved["EditSequence"] = str(100 + self.writes)
            for field in node:
                if field.tag in ("ListID", "EditSequence"):
                    continue
                if field.tag == "Name":
                    self.saved["Name"] = field.text
                    if self.kind != "supplier":
                        self.saved["FullName"] = field.text
                elif field.tag in ("SalesOrPurchaseMod", "SalesAndPurchaseMod"):
                    self.saved.setdefault(field.tag.removesuffix("Mod"), {}).update(
                        masters.record(field)
                    )
                elif field.tag.endswith("Ref"):
                    self.saved[field.tag] = masters.record(field)
                else:
                    self.saved[field.tag] = masters.record(field)
            result = ET.Element("QBXML")
            batch = ET.SubElement(result, "QBXMLMsgsRs")
            rs = ET.SubElement(
                batch,
                rq.tag[:-2] + "Rs",
                requestID=rq.get("requestID"),
                statusCode="0",
                statusSeverity="Info",
            )
            rec = ET.SubElement(rs, masters.KINDS[self.kind] + "Ret")

            def append(parent, value):
                for name, v in value.items():
                    child = ET.SubElement(parent, name)
                    if isinstance(v, dict):
                        append(child, v)
                    else:
                        child.text = str(v)

            append(rec, self.saved)
            answer = ET.tostring(result, encoding="unicode")
            if self.lost:
                raise RuntimeError("lost master response")
            (folder / "add.response.xml").write_text(answer)
            return answer
        finally:
            (folder / "closed.txt").write_text("closed")


@pytest.fixture
def configured(master_case):
    b, t, _ = master_case
    path = Path(b.config_path)
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] += ["post-sample"]
    raw["companies"]["company-a"].update(
        approval_required=False,
        sample_master_posting={
            "connector": "connector-company-a",
            "authorization": "Explicit controlled synthetic master qualification",
            "name_prefix": "TEST ",
            "max_writes": 8,
            "expires_at": time.time() + 3600,
            "kinds": list(masters.KINDS),
        },
    )
    path.write_text(json.dumps(raw))
    return b, t


def prepare(b, t, payload, native, ref="master-one"):
    observed = checks.read(
        b,
        t,
        "company-a",
        "connector-company-a",
        payload["kind"],
        payload=payload,
        transport=native.read,
    )
    job = b.prepare(
        t,
        "company-a",
        {
            "operation": "master.change",
            "surface": "browser",
            "idempotency_key": ref,
            "payload": payload,
            "source": {
                "namespace": "synthetic-intake",
                "reference": ref,
                "sha256": "a" * 64,
                "original_values": {"name": "TEST Master"},
                "uncertain_fields": [],
            },
            "master_evidence": observed["reference"],
        },
    )
    b.action(t, "company-a", job["id"], "validate")
    b.action(t, "company-a", job["id"], "submit")
    return job["id"]


@pytest.mark.parametrize("kind", masters.KINDS)
def test_native_create_update_and_duplicate_rejection(configured, kind):
    b, t = configured
    native = Native(kind)
    job = prepare(b, t, proposal(kind), native)
    saved = posting.post(b, t, "company-a", job, transport=native, read_transport=native.read)
    assert saved["state"] == "verified" and saved["transaction_receipt"]["action"] == "create"
    assert native.writes == 1
    with pytest.raises(BridgeError, match="never retry"):
        posting.post(b, t, "company-a", job, transport=native)
    with pytest.raises(BridgeError, match="already exists"):
        prepare(b, t, proposal(kind), native, "duplicate")
    value = proposal(kind)
    value.update(
        action="update",
        target=masters.target_reference(native.saved),
        fields={"phone": "555-0101"}
        if kind in ("customer", "supplier")
        else {"discount_amount": "2.00"}
        if kind == "discount"
        else {"sales_price": "7.00"},
    )
    value["ref_number"] = "MASTER-002"
    update = prepare(b, t, value, native, "master-update")
    result = posting.post(b, t, "company-a", update, transport=native, read_transport=native.read)
    assert (
        result["state"] == "verified" and result["txn_id"] == saved["txn_id"] and native.writes == 2
    )
    assert b.audit(t, "company-a")["valid"]


@pytest.mark.parametrize("kind", masters.KINDS)
def test_missing_response_reconciles_without_resend(configured, kind):
    b, t = configured
    native = Native(kind, lost=True)
    job = prepare(b, t, proposal(kind), native)
    with pytest.raises(RuntimeError, match="lost"):
        posting.post(b, t, "company-a", job, transport=native)
    assert b.status(t, "company-a", job)["state"] == "unknown"
    assert (
        posting.reconcile(Bridge(b.config_path), t, "company-a", job, transport=native.read)[
            "state"
        ]
        == "verified"
    )
    assert native.writes == 1


def test_revoked_approval_before_native_write(configured):
    b, t = configured
    native = Native("customer")

    def revoke():
        path = Path(b.config_path)
        raw = json.loads(path.read_text())
        raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
            "post-sample"
        )
        path.write_text(json.dumps(raw))

    native.before = revoke
    job = prepare(b, t, proposal("customer"), native)
    with pytest.raises(BridgeError):
        posting.post(b, t, "company-a", job, transport=native)
    assert native.writes == 0


def test_preexisting_business_record_cannot_use_sample_update_gate(configured):
    b, t = configured
    native = Native("customer")
    native.saved = existing("customer")
    value = proposal("customer")
    value.update(
        action="update", target=masters.target_reference(native.saved), fields={"phone": "555-0101"}
    )
    job = prepare(b, t, value, native)
    with pytest.raises(BridgeError, match="Bridge-created"):
        posting.post(b, t, "company-a", job, transport=native)
    assert native.writes == 0 and b.status(t, "company-a", job)["state"] == "queued"


def test_gate_expiration_and_quota_validated(configured):
    b, t = configured
    native = Native("customer")
    job = prepare(b, t, proposal("customer"), native)
    b.clock = lambda: time.time() + 7200
    with pytest.raises(BridgeError, match="expired"):
        posting.post(b, t, "company-a", job, transport=native)
    for patch in (
        {"max_writes": 0},
        {"expires_at": float("nan")},
        {"kinds": ["raw"]},
        {"name_prefix": ""},
    ):
        value = dict(
            Config.load(b.config_path).companies["company-a"].sample_master_posting, **patch
        )
        with pytest.raises(BridgeError):
            posting.validate_gate(value)


def test_altered_write_intent_cannot_reconcile(configured):
    b, t = configured
    native = Native("customer", lost=True)
    job = prepare(b, t, proposal("customer"), native)
    with pytest.raises(RuntimeError, match="lost"):
        posting.post(b, t, "company-a", job, transport=native)
    _, _, _, store = b._context(t, "company-a", "read")
    attempt = b.status(t, "company-a", job)["attempt"]
    (store.path.parent / ("native-master-" + attempt) / "write-intent.txt").write_text("0" * 64)
    with pytest.raises(BridgeError, match="write intent"):
        posting.reconcile(b, t, "company-a", job, transport=native.read)
    assert b.status(t, "company-a", job)["state"] == "unknown" and native.writes == 1


def test_stale_target_at_final_authorization_prevents_update(configured):
    b, t = configured
    native = Native("supplier")
    job = prepare(b, t, proposal("supplier"), native)
    posting.post(b, t, "company-a", job, transport=native, read_transport=native.read)
    value = proposal("supplier")
    value.update(
        action="update", target=masters.target_reference(native.saved), fields={"phone": "555-0102"}
    )
    value["ref_number"] = "MASTER-002"
    update = prepare(b, t, value, native, "stale-update")
    native.saved["EditSequence"] = "999"
    with pytest.raises(BridgeError, match="stale master"):
        posting.post(b, t, "company-a", update, transport=native)
    assert native.writes == 1
