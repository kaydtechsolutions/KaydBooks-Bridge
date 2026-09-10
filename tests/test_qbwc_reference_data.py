"""Complete read-only reference-data lifecycle through QBWC."""

from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.hermes_tools import server
from kaydbooks_bridge.qbwc import DurableQBWCDiscoveryService as S
from kaydbooks_bridge.qbwc_reference_data import read
from kaydbooks_bridge.reference_data import QUERIES, append_queries, plan, validate_response
from kaydbooks_bridge.service import Bridge
from qbwc_kit.testing import FakeQuickBooks
from test_direct_sdk import direct  # noqa: F401
from test_native_reports import case  # noqa: F401
from test_qbwc_discovery import (
    COMPANY_A,
    HOST,
    authenticate,
    call,
    discovery_setup,  # noqa: F401
    hcp_for,
    receive,
)


def response(request):
    requests = list(ET.fromstring(request)[0])
    run = requests[2].attrib["requestID"][:-1]
    root = ET.fromstring(
        FakeQuickBooks(
            entities={"Host": [{**HOST, "SupportedQBXMLVersion": ["17.0"]}], "Company": [COMPANY_A]}
        )(S._discovery_request(run, "17.0"))
    )
    for index, (_, entity, _) in enumerate(QUERIES, start=3):
        answer = ET.SubElement(
            root[0],
            entity + "QueryRs",
            requestID=run + str(index),
            statusCode="0",
            statusSeverity="Info",
        )
        record = ET.SubElement(answer, entity + "Ret")
        ET.SubElement(record, "ListID").text = f"id-{index}"
        ET.SubElement(record, "Initial" if entity == "SalesRep" else "Name").text = (
            "JS" if entity == "SalesRep" else f"Record {index}"
        )
        ET.SubElement(record, "IsActive").text = "true"
    return ET.tostring(root, encoding="unicode")


def request(path, token, request_id="references-001"):
    return read(Bridge(path), token, "company-a", "connector-company-a", request_id)


def test_reference_data_request_is_read_only_and_complete(case):  # noqa: F811
    path, token = case
    assert request(path, token)["pending"] is True
    service = S.from_path(path)
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
    assert all(entity + "QueryRq" in xml for _, entity, _ in QUERIES)
    assert "<ActiveStatus>All</ActiveStatus>" in xml
    assert not any(word in xml for word in ("AddRq", "ModRq", "DelRq"))
    assert receive(service, ticket, response(xml)) == 100
    call(service, "closeConnection", ticket=ticket)
    result = request(path, token)
    assert result["pending"] is False
    assert result["complete"] is True and result["read_only"] is True
    assert result["company_display_name"] == "Synthetic Company A"
    assert result["counts"]["accounts"] == 1
    assert result["counts"]["items"] == sum(catalog == "items" for catalog, _, _ in QUERIES)
    assert result["counts"]["terms"] == 2
    with service._stores["company-a"].transaction() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert service._stores["company-a"].verify_audit(db)


def test_reference_data_rejects_incomplete_or_duplicate_lists(case):  # noqa: F811
    policy = S.from_path(case[0]).config.companies["company-a"]
    check = plan(policy, {})
    run = "123456789"
    xml = append_queries(S._discovery_request(run, "17.0"), run, check)
    answer = response(xml)
    root = ET.fromstring(answer)
    root[0].remove(root[0][-1])
    with pytest.raises(BridgeError, match="complete"):
        validate_response(ET.tostring(root, encoding="unicode"), run, check)
    root = ET.fromstring(answer)
    root[0][-1].find("./ItemSalesTaxGroupRet/ListID").text = (
        root[0][-2].find("./ItemSalesTaxRet/ListID").text
    )
    with pytest.raises(BridgeError, match="duplicate"):
        validate_response(ET.tostring(root, encoding="unicode"), run, check)


def test_reference_data_tool_schema_is_explicit(case):  # noqa: F811
    import asyncio

    app = server(*case)
    tools = asyncio.run(app.list_tools())
    schema = next(tool.inputSchema for tool in tools if tool.name == "qbwc_reference_data_v1")
    assert set(schema["required"]) == {"company", "connector_id", "request_id"}
