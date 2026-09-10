"""Reviewed dispatch interruption, replay, identity, and saved-field checks."""
# ruff: noqa: F811

import copy
import json
import time
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.reviewed_qbwc import ReviewedQBWCService as S
from kaydbooks_bridge.reviewed_qbwc import stage
from kaydbooks_bridge.reviewed_requests import build, verify
from kaydbooks_bridge.service import Bridge
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import (
    COMPANY_A,
    COMPANY_B,
    HOST,
    authenticate,
    call,
    discovery_setup,  # noqa: F401
    hcp_for,
    receive,
)  # noqa: F401


@pytest.fixture
def prepared(direct, monkeypatch):
    path, token = direct
    raw = json.loads(path.read_text())
    actor = next(iter(raw["principals"]))
    raw["principals"][actor]["companies"]["company-a"] = [
        "read",
        "prepare",
        "validate",
        "submit",
        "approve",
        "post-sample",
        "pause",
        "recover",
    ]
    raw["connectors"]["connector-company-a"]["company_file_env"] = "KAYDBOOKS_TEST_COMPANY_FILE"
    monkeypatch.setenv("KAYDBOOKS_TEST_COMPANY_FILE", r"C:\Synthetic\CompanyA.QBW")
    path.write_text(json.dumps(raw))
    approved = {
        "batch_id": "reviewed-test",
        "company": "company-a",
        "connector": "connector-company-a",
        "identity_sha256": Config.load(path).connectors["connector-company-a"].identity_sha256,
        "authorization": "Owner authorized this exact synthetic batch",
        "expires_at": time.time() + 3600,
        "entries": [
            {"sequence": 1, "operation": "customer.create", "name": "Synthetic New Customer"}
        ],
        "maps": {},
    }
    stage(Bridge(path), token, "company-a", approved)
    Bridge(path).pause(token, "company-a", False)
    return path, token, approved


def send(s, ticket, company_file=r"C:\Synthetic\CompanyA.QBW", hcp=None):
    return call(
        s,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse=hcp or hcp_for(),
        strCompanyFileName=company_file,
        qbXMLCountry="US",
        qbXMLMajorVers="17",
        qbXMLMinorVers="0",
    )


def response(request, saved=None, company=COMPANY_A):
    root = E.Element("QBXML")
    batch = E.SubElement(root, "QBXMLMsgsRs")
    for query in E.fromstring(request)[0]:
        tag = query.tag[:-2] + "Rs"
        if query.tag in ("HostQueryRq", "CompanyQueryRq"):
            single = E.Element("QBXML")
            E.SubElement(single, "QBXMLMsgsRq").append(query)
            answer = E.fromstring(
                FakeQuickBooks(
                    entities={
                        "Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}],
                        "Company": [company],
                    }
                )(E.tostring(single, encoding="unicode"))
            )[0][0]
            batch.append(answer)
            continue
        rs = E.SubElement(
            batch, tag, requestID=query.get("requestID"), statusCode="0", statusSeverity="Info"
        )
        if query.tag == "PreferencesQueryRq":
            prefs = E.SubElement(rs, "PreferencesRet")
            E.SubElement(
                E.SubElement(prefs, "MultiCurrencyPreferences"), "IsMultiCurrencyOn"
            ).text = "false"
        elif query.tag == "CustomerAddRq":
            row = copy.deepcopy(query[0])
            row.tag = "CustomerRet"
            E.SubElement(row, "ListID").text = "new-id"
            E.SubElement(row, "EditSequence").text = "1"
            rs.append(row)
        elif saved is not None:
            rs.append(copy.deepcopy(saved))
    return E.tostring(root, encoding="unicode")


def test_saved_readback_required_and_no_resend(prepared):
    path, _, _ = prepared
    s = S.from_path(path)
    ticket, _ = authenticate(s)
    pre = send(s, ticket)
    assert "AddRq" not in pre
    assert receive(s, ticket, response(pre)) == 0
    write = send(s, ticket)
    assert "CustomerAddRq" in write
    add = response(write)
    assert receive(s, ticket, add) == 0
    read = send(s, ticket)
    assert "<ListID>new-id</ListID>" in read and "AddRq" not in read
    saved = E.fromstring(add)[0][0][0]
    assert receive(s, ticket, response(read, saved)) == 100
    with s._stores["company-a"].transaction() as db:
        assert db.execute("SELECT state FROM reviewed_batches").fetchone()[0] == "verified"
        assert (
            db.execute("SELECT count(*) FROM reviewed_steps WHERE phase='write'").fetchone()[0] == 1
        )
        assert db.execute("SELECT record_id FROM reviewed_results").fetchone()[0] == "new-id"


def test_interrupted_handout_is_never_reissued(prepared):
    path, _, _ = prepared
    s = S.from_path(path)
    ticket, _ = authenticate(s)
    pre = send(s, ticket)
    receive(s, ticket, response(pre))
    assert "AddRq" in send(s, ticket)
    restarted = S.from_path(path)
    assert send(restarted, ticket) == ""
    with restarted._stores["company-a"].transaction() as db:
        assert db.execute("SELECT state FROM reviewed_batches").fetchone()[0] == "held"
        assert db.execute("SELECT paused FROM control").fetchone()[0] == 1


def test_acknowledged_write_recovery_only_reads(prepared, monkeypatch):
    import kaydbooks_bridge.reviewed_qbwc as module

    path, token, approved = prepared
    service = S.from_path(path)
    ticket, _ = authenticate(service)
    pre = send(service, ticket)
    receive(service, ticket, response(pre))
    write = send(service, ticket)
    with monkeypatch.context() as patch:

        def fail(*args):
            raise BridgeError("verification adapter failure")

        patch.setattr(module, "verify", fail)
        assert receive(service, ticket, response(write)) == -1
    module.resume_saved_readback(Bridge(path), token, "company-a", approved["batch_id"])
    Bridge(path).pause(token, "company-a", False)
    service = S.from_path(path)
    ticket, _ = authenticate(service)
    read = send(service, ticket)
    assert "AddRq" not in read and "<ListID>new-id</ListID>" in read
    saved = E.fromstring(response(write))[0][0][0]
    assert receive(service, ticket, response(read, saved)) == 100
    with service._stores["company-a"].transaction() as db:
        assert (
            db.execute("SELECT count(*) FROM reviewed_steps WHERE phase='write'").fetchone()[0] == 1
        )


def test_payment_return_uses_amount_and_preserves_discount():
    from kaydbooks_bridge.reviewed_requests import compare

    request = E.fromstring(
        "<AppliedToTxnAdd><TxnID>invoice</TxnID><PaymentAmount>506</PaymentAmount><DiscountAmount>0.60</DiscountAmount><DiscountAccountRef><ListID>discount</ListID></DiscountAccountRef></AppliedToTxnAdd>"
    )
    saved = E.fromstring(
        "<AppliedToTxnRet><TxnID>invoice</TxnID><Amount>506.00</Amount><DiscountAmount>0.60</DiscountAmount><DiscountAccountRef><ListID>discount</ListID></DiscountAccountRef></AppliedToTxnRet>"
    )
    compare(request, saved)
    saved.find("DiscountAmount").text = "0.61"
    with pytest.raises(BridgeError):
        compare(request, saved)


def test_subsequent_callback_reuses_verified_session_identity(prepared):
    path, _, _ = prepared
    service = S.from_path(path)
    ticket, _ = authenticate(service)
    request = send(service, ticket)
    assert receive(service, ticket, response(request)) == 0
    xml = call(
        service,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse="",
        strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
        qbXMLCountry="US",
        qbXMLMajorVers="17",
        qbXMLMinorVers="0",
    )
    assert "CustomerAddRq" in xml


@pytest.mark.parametrize("fault", ["path", "identity", "permission", "collision", "readback"])
def test_failure_holds_batch(prepared, fault):
    path, _, _ = prepared
    s = S.from_path(path)
    ticket, _ = authenticate(s)
    if fault == "path":
        assert send(s, ticket, company_file=r"C:\Other.qbw") == ""
        return
    if fault == "permission":
        raw = json.loads(path.read_text())
        next(iter(raw["principals"].values()))["companies"]["company-a"].remove("approve")
        path.write_text(json.dumps(raw))
        assert send(s, ticket) == ""
        return
    pre = send(s, ticket)
    if fault == "identity":
        assert receive(s, ticket, response(pre, company=COMPANY_B)) == -1
        return
    if fault == "collision":
        old = E.fromstring(
            "<CustomerRet><ListID>old-id</ListID><Name>Synthetic New Customer</Name></CustomerRet>"
        )
        assert receive(s, ticket, response(pre, old)) == -1
        return
    receive(s, ticket, response(pre))
    write = send(s, ticket)
    add = response(write)
    receive(s, ticket, add)
    read = send(s, ticket)
    saved = E.fromstring(add)[0][0][0]
    saved.find("Name").text = "Wrong Customer"
    assert receive(s, ticket, response(read, saved)) == -1


def test_invoice_explicit_fields_are_not_silently_dropped():
    maps = {
        group: {name: {"ListID": name + "-id", "IsActive": "true"}}
        for group, name in [
            ("customers", "buyer"),
            ("items", "item"),
            ("terms", "terms"),
            ("sales_reps", "rep"),
            ("inventory_sites", "site"),
        ]
    }
    maps.update(receivable_id="ar-id", non_tax_code_id="non-id")
    entry = {
        "operation": "invoice.create",
        "source_reference": "A-1",
        "txn_date": "2026-01-01",
        "customer_full_name": "buyer",
        "terms_full_name": "terms",
        "sales_rep_initials": "rep",
        "inventory_site_full_name": "site",
        "total_amount": "2.00",
        "lines": [
            {
                "item_full_name": "item",
                "quantity": "2",
                "unit_price": "1.00",
                "amount": "2.00",
                "description": "Exact note",
            }
        ],
    }
    row = E.fromstring(build(entry, maps, "1"))[0][0][0]
    assert row.findtext("TermsRef/ListID") == "terms-id"
    assert row.findtext("SalesRepRef/ListID") == "rep-id"
    assert row.findtext("InvoiceLineAdd/InventorySiteRef/ListID") == "site-id"
    assert row.findtext("InvoiceLineAdd/Desc") == "Exact note"
    row.tag = "InvoiceRet"
    row.find("InvoiceLineAdd").tag = "InvoiceLineRet"
    for tag, value in [
        ("TxnID", "txn-id"),
        ("EditSequence", "1"),
        ("SalesTaxTotal", "0.00"),
        ("Subtotal", "2.00"),
        ("AppliedAmount", "0.00"),
    ]:
        E.SubElement(row, tag).text = value
    assert verify(entry, maps, row) == "txn-id"
    row.find("InvoiceLineRet/Desc").text = "Changed note"
    with pytest.raises(BridgeError):
        verify(entry, maps, row)
