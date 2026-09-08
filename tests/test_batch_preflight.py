"""Exact-reference reads retain multiple payment legs without permitting writes."""

from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.batch_preflight import plan, validate_response
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.qbwc_invoices import invoice_job
from test_direct_sdk import direct  # noqa: F401
from test_native_reports import case  # noqa: F401
from test_qbwc_discovery import authenticate, call, discovery_setup, hcp_for, receive  # noqa: F401
from test_qbwc_reference_data import response as reference_response

SPEC = {"transactions": [{"entity": "ReceivePayment", "ref_number": "PAY-01"}]}


def answer(run="123456789", count=2):
    from kaydbooks_bridge.reference_data import append_queries as reference_queries
    from test_qbwc_reference_data import QUERIES

    root = ET.fromstring(
        reference_response(reference_queries(S._discovery_request(run, "17.0"), run, {}))
    )
    assert len(root[0]) == len(QUERIES) + 2
    for node in list(root[0])[2:]:
        root[0].remove(node)
    rs = ET.SubElement(
        root[0], "ReceivePaymentQueryRs", requestID=run + "3", statusCode="0", statusSeverity="Info"
    )
    for index in range(count):
        row = ET.SubElement(rs, "ReceivePaymentRet")
        ET.SubElement(row, "TxnID").text = f"txn-{index}"
        ET.SubElement(row, "RefNumber").text = "PAY-01"
    return ET.tostring(root, encoding="unicode")


def test_read_lifecycle_retains_shared_reference(case):  # noqa: F811
    path, token = case
    service = S.from_path(path)
    assert (
        invoice_job(
            service,
            token,
            "connector-company-a",
            "preflight-01",
            payload=SPEC,
            enqueue=True,
            operation="batch-preflight.read",
        )["state"]
        == "queued"
    )
    ticket, _ = authenticate(service)
    xml = call(
        service,
        "sendRequestXML",
        ticket=ticket,
        strHCPResponse=hcp_for(),
        strCompanyFileName=r"C:\Synthetic\CompanyA.QBW",
        qbXMLCountry="US",
        qbXMLMajorVers="17",
        qbXMLMinorVers="0",
    )
    assert "<RefNumber>PAY-01</RefNumber>" in xml
    assert not any(value in xml for value in ("AddRq", "ModRq", "DelRq"))
    run = ET.fromstring(xml)[0][2].get("requestID")[:-1]
    assert receive(service, ticket, answer(run)) == 100
    call(service, "closeConnection", ticket=ticket)
    result = invoice_job(
        service, token, "connector-company-a", "preflight-01", operation="batch-preflight.read"
    )
    assert result["company_identity_verified"] and result["read_only"]
    assert len(result["transactions"][0]["records"]) == 2
    with service._stores["company-a"].transaction() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert service._stores["company-a"].verify_audit(db)


def test_selectors_and_responses_fail_closed(case):  # noqa: F811
    policy = S.from_path(case[0]).config.companies["company-a"]
    check = plan(policy, SPEC)
    for spec in (
        {"transactions": []},
        {"transactions": SPEC["transactions"] * 2},
        {"transactions": [{"entity": "InvoiceAdd", "ref_number": "X"}]},
    ):
        with pytest.raises(BridgeError):
            plan(policy, spec)
    for mutation in ("reference", "identity", "correlation", "missing", "error"):
        root = ET.fromstring(answer())
        if mutation == "reference":
            root[0][-1][0].find("RefNumber").text = "OTHER"
        elif mutation == "identity":
            root[0][-1][1].find("TxnID").text = "txn-0"
        elif mutation == "correlation":
            root[0][-1].set("requestID", "wrong")
        elif mutation == "missing":
            root[0].remove(root[0][-1])
        else:
            root[0][-1].set("statusCode", "3100")
        with pytest.raises(BridgeError):
            validate_response(ET.tostring(root), "123456789", check)
