"""Master changes use explicit fields, original snapshots and shared review lifecycle."""

# ruff: noqa: F811
import copy
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge import master_checks as checks
from kaydbooks_bridge import master_records as masters
from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.service import Bridge
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import COMPANY_A, HOST, discovery_setup  # noqa: F401

GUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def master_case(direct):
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] += [
        "prepare",
        "validate",
        "submit",
        "approve",
    ]
    raw["companies"]["company-a"]["account_roles"] = {
        "master_income": "income-id",
        "master_expense": "expense-id",
        "master_cogs": "cogs-id",
        "master_asset": "asset-id",
    }
    path.write_text(json.dumps(raw))
    return Bridge(path), token, Config.load(path).companies["company-a"]


def proposal(kind):
    fields = {"name": "TEST Master", "active": True}
    if kind in ("customer", "supplier"):
        fields.update(company_name="Test Company", phone="555-0100", email="test@example.invalid")
    else:
        fields.update(
            sales_description="Test service", sales_price="5.00", income_account="master_income"
        )
        if kind == "inventory":
            fields.update(
                purchase_cost="2.00", cogs_account="master_cogs", asset_account="master_asset"
            )
    return {
        "ref_number": "MASTER-001",
        "kind": kind,
        "action": "create",
        "fields": fields,
        **({"service_mode": "sales"} if kind == "service" else {}),
    }


def existing(kind):
    value = {
        "ListID": "master-id",
        "EditSequence": "100",
        "Name": "TEST Master",
        "IsActive": "true",
        "ExternalGUID": "{" + GUID.upper() + "}",
    }
    if kind != "supplier":
        value["FullName"] = value["Name"]
    if kind in ("customer", "supplier"):
        value.update(
            CompanyName="Test Company",
            Phone="555-0100",
            Email="test@example.invalid",
            Balance="0.00",
        )
    elif kind == "service":
        value["SalesOrPurchase"] = {
            "Desc": "Test service",
            "Price": "5.00",
            "AccountRef": {"ListID": "income-id", "FullName": "Income"},
        }
    else:
        value.update(
            SalesDesc="Test service",
            SalesPrice="5.00",
            PurchaseCost="2.00",
            IncomeAccountRef={"ListID": "income-id"},
            COGSAccountRef={"ListID": "cogs-id"},
            AssetAccountRef={"ListID": "asset-id"},
            QuantityOnHand="0",
            AverageCost="0",
        )
    return value


def native_response(request, records=None, company=COMPANY_A):
    entities = {
        "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
        "Company": [company],
        "Preferences": [{"MultiCurrencyPreferences": {"IsMultiCurrencyOn": "false"}}],
        "Account": [
            {"ListID": "income-id", "IsActive": "true", "AccountType": "Income"},
            {"ListID": "expense-id", "IsActive": "true", "AccountType": "Expense"},
            {"ListID": "cogs-id", "IsActive": "true", "AccountType": "CostOfGoodsSold"},
            {"ListID": "asset-id", "IsActive": "true", "AccountType": "OtherCurrentAsset"},
        ],
        **(records or {}),
    }
    text = FakeQuickBooks(entities=entities)(request)
    root = ET.fromstring(text)
    for rq, rs in zip(ET.fromstring(request)[0], root[0], strict=True):
        # Observed Enterprise 24 behavior: selecting item return fields omits
        # ExternalGUID even when it appears in IncludeRetElement.
        if rq.tag in ("ItemServiceQueryRq", "ItemInventoryQueryRq") and rq.findall(
            "IncludeRetElement"
        ):
            for rec in rs:
                for guid in rec.findall("ExternalGUID"):
                    rec.remove(guid)
        if rq.findtext("ListID"):
            for child in list(rs):
                if child.findtext("ListID") != rq.findtext("ListID"):
                    rs.remove(child)
        if rs.tag in ("EntityQueryRs", "ItemQueryRs") and not len(rs):
            rs.set("statusCode", "500")
            rs.set("statusSeverity", "Warn")
    return ET.tostring(root, encoding="unicode")


def transport(records=None, before=None):
    def send(request, folder, callback):
        folder.mkdir()
        text = native_response(request, records)
        (folder / "preflight.response.xml").write_text(text)
        (folder / "closed.txt").write_text("closed")
        if before:
            before()
        callback(text)

    return send


@pytest.mark.parametrize("kind", masters.KINDS)
def test_create_shapes_preserved_balance_and_field_comparison(master_case, kind):
    _, _, p = master_case
    value = proposal(kind)
    request = masters.request(value, p, "100", external_guid=GUID)
    node = ET.fromstring(request)[0][0][0]
    assert node.tag == masters.KINDS[kind] + "Add"
    assert node.findtext("ExternalGUID") == "{" + GUID.upper() + "}"
    assert not any(
        n.tag in {"OpenBalance", "QuantityOnHand", "TotalValue", "CreditCardInfo"}
        for n in node.iter()
    )
    saved = existing(kind)
    assert masters.compare(value, p, saved)["list_id"] == "master-id"
    changed = copy.deepcopy(saved)
    changed["Balance"] = "1.00"
    with pytest.raises(BridgeError, match="opening"):
        masters.compare(value, p, changed)


@pytest.mark.parametrize("kind", masters.KINDS)
def test_updates_keep_edit_sequence_and_unmentioned_values(master_case, kind):
    _, _, p = master_case
    prior = existing(kind)
    value = proposal(kind)
    value.update(
        action="update", target=masters.target_reference(prior), fields={"name": "TEST Revised"}
    )
    request = masters.request(value, p, "100")
    node = ET.fromstring(request)[0][0][0]
    assert node.findtext("ListID") == "master-id" and node.findtext("EditSequence") == "100"
    assert set(n.tag for n in node) == {"ListID", "EditSequence", "Name"}
    saved = copy.deepcopy(prior)
    saved.update(Name="TEST Revised", EditSequence="101")
    if kind != "supplier":
        saved["FullName"] = "TEST Revised"
    assert masters.compare(value, p, saved, prior)["edit_sequence"] == "101"
    saved["Balance"] = "10.00"
    if kind in ("customer", "supplier"):
        with pytest.raises(BridgeError, match="preserved"):
            masters.compare(value, p, saved, prior)


def test_native_contact_duplicates_outside_review_projection_are_retained_only_in_raw_response():
    text = '<QBXML><QBXMLMsgsRs><CustomerAddRs requestID="100" statusCode="0" statusSeverity="Info"><CustomerRet><ListID>master-id</ListID><EditSequence>1</EditSequence><Name>TEST Master</Name><Phone>555-0100</Phone><AdditionalContactRef><ContactName>Main Phone</ContactName><ContactValue>555-0100</ContactValue></AdditionalContactRef><AdditionalContactRef><ContactName>Main Email</ContactName><ContactValue>test@example.invalid</ContactValue></AdditionalContactRef></CustomerRet></CustomerAddRs></QBXMLMsgsRs></QBXML>'
    saved = masters.response(text, "customer", "Add", "100")
    assert saved["Phone"] == "555-0100" and "AdditionalContactRef" not in saved
    with pytest.raises(BridgeError, match="repeated"):
        masters.response(
            text.replace("</CustomerRet>", "<Phone>555-0101</Phone></CustomerRet>"),
            "customer",
            "Add",
            "100",
        )


def test_fresh_check_prepare_validate_and_preview(master_case):
    b, t, p = master_case
    payload = proposal("customer")
    check = checks.read(
        b, t, p.id, "connector-company-a", "customer", payload=payload, transport=transport()
    )
    source = {
        "namespace": "synthetic-intake",
        "reference": "master-source",
        "sha256": "a" * 64,
        "original_values": {"name": "TEST Master"},
        "uncertain_fields": [],
    }
    envelope = {
        "operation": "master.change",
        "surface": "browser",
        "idempotency_key": "master-one",
        "payload": payload,
        "source": source,
        "master_evidence": check["reference"],
    }
    job = b.prepare(t, p.id, envelope)
    assert job["master_evidence"]["original"] is None
    b.action(t, p.id, job["id"], "validate")
    assert b.preview(t, p.id, job["id"])["schema"] == "master-review-v1"
    assert b.prepare(t, p.id, envelope)["id"] == job["id"]
    assert b.audit(t, p.id)["valid"]


def test_stale_edit_sequence_and_collision_rejected_before_preparation(master_case):
    b, t, p = master_case
    original = existing("customer")
    value = proposal("customer")
    value.update(
        action="update", target=masters.target_reference(original), fields={"phone": "555-0101"}
    )
    changed = {**original, "EditSequence": "101"}
    with pytest.raises(BridgeError, match="stale"):
        checks.read(
            b,
            t,
            p.id,
            "connector-company-a",
            "customer",
            payload=value,
            transport=transport({"Customer": [changed]}),
        )
    with pytest.raises(BridgeError, match="already exists"):
        checks.read(
            b,
            t,
            p.id,
            "connector-company-a",
            "customer",
            payload=proposal("customer"),
            transport=transport({"Entity": [original]}),
        )


def test_late_revocation_and_old_cached_evidence(master_case):
    b, t, p = master_case
    check = checks.read(
        b,
        t,
        p.id,
        "connector-company-a",
        "customer",
        payload=proposal("customer"),
        transport=transport(),
        run_id="123",
    )
    b.clock = lambda: 0
    with pytest.raises(BridgeError, match="stale"):
        _, a, _, store = b._context(t, p.id, "read")
        with store.transaction() as db:
            checks.resolve(
                Config.load(b.config_path),
                p,
                store,
                db,
                a,
                proposal("customer"),
                check["reference"],
                b.clock(),
            )

    def revoke():
        path = Path(b.config_path)
        raw = json.loads(path.read_text())
        raw["principals"][next(iter(raw["principals"]))]["companies"][p.id].remove("validate")
        path.write_text(json.dumps(raw))

    with pytest.raises(BridgeError):
        checks.read(
            b,
            t,
            p.id,
            "connector-company-a",
            "customer",
            payload=proposal("customer"),
            transport=transport(before=revoke),
        )


@pytest.mark.parametrize(
    "field", ["open_balance", "quantity_on_hand", "ssn", "raw_xml", "income_account"]
)
def test_update_cannot_change_history_or_accept_commands(master_case, field):
    _, _, p = master_case
    value = proposal("customer")
    value.update(
        action="update",
        target=masters.target_reference(existing("customer")),
        fields={field: "bad"},
    )
    with pytest.raises(BridgeError):
        masters.validate(value, p)


@pytest.mark.skipif(os.name != "nt", reason="Windows native compiler")
def test_compiled_master_write_gate(master_case, tmp_path):
    _, _, p = master_case
    values = [
        masters.request(proposal(kind), p, "100", external_guid=GUID) for kind in masters.KINDS
    ]
    for kind in masters.KINDS:
        value = proposal(kind)
        value.update(
            action="update",
            target=masters.target_reference(existing(kind)),
            fields={"name": "TEST Revised"},
        )
        values.append(masters.request(value, p, "100"))
    source = Path("src/kaydbooks_bridge/native_master.ps1").read_text()
    methods = source[
        source.index(" static XmlDocument Parse(") : source.index(" public static void Run(")
    ]
    data = tmp_path / "inputs.json"
    data.write_text(json.dumps(values))
    script = tmp_path / "gate.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nAdd-Type -ReferencedAssemblies @('System.Xml.dll','System.Core.dll') -TypeDefinition @'\nusing System;using System.Xml;using System.IO;using System.Text;using System.Security.Cryptography;public static class Gate {\n"
        + methods
        + "}\n'@\nforeach($v in (Get-Content -Raw $args[0]|ConvertFrom-Json)){[Gate]::CheckWrite($v,[Gate]::Hash($v));$bad=$v.Replace('<IsActive>true</IsActive>','<OpenBalance>99.00</OpenBalance>');if($bad -ne $v){$accepted=$true;try{[Gate]::CheckWrite($bad,[Gate]::Hash($bad))}catch{$accepted=$false};if($accepted){throw 'opening balance accepted'}}}\n"
    )
    result = subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-File",
            str(script),
            str(data),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
