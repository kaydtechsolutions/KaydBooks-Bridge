from xml.etree import ElementTree as ET

import pytest

from qbwc_kit.wsdl import build_qwc, build_wsdl

WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
QBWC_METHODS = [
    "serverVersion",
    "clientVersion",
    "authenticate",
    "sendRequestXML",
    "receiveResponseXML",
    "connectionError",
    "getLastError",
    "closeConnection",
]


@pytest.fixture(scope="module")
def wsdl():
    return build_wsdl("https://books.example.com/qbwc")


def test_wsdl_is_well_formed(wsdl):
    assert ET.fromstring(wsdl).tag == f"{{{WSDL_NS}}}definitions"


def test_declares_every_qbwc_operation(wsdl):
    root = ET.fromstring(wsdl)
    port_type = root.find(f"{{{WSDL_NS}}}portType")
    declared = [op.get("name") for op in port_type]
    assert declared == QBWC_METHODS


def test_each_operation_has_in_and_out_messages(wsdl):
    root = ET.fromstring(wsdl)
    messages = {m.get("name") for m in root.findall(f"{{{WSDL_NS}}}message")}
    for method in QBWC_METHODS:
        assert f"{method}SoapIn" in messages
        assert f"{method}SoapOut" in messages


def test_endpoint_address_is_the_url_qbwc_will_post_to(wsdl):
    assert 'location="https://books.example.com/qbwc"' in wsdl


def test_authenticate_returns_a_string_array(wsdl):
    assert 'name="authenticateResult" type="tns:ArrayOfString"' in wsdl


def test_receive_response_returns_an_int(wsdl):
    assert 'name="receiveResponseXMLResult" type="s:int"' in wsdl


def test_qwc_file_is_well_formed_and_carries_the_ids():
    qwc = build_qwc(
        app_name="Books Bridge",
        app_id="",
        app_url="https://books.example.com/qbwc",
        app_description="Syncs customers",
        username="qbwc",
        owner_id="{57F3B9B0-86F1-4fcc-B1EE-566DE1813D20}",
        file_id="{57F3B9B0-86F1-4fcc-B1EE-566DE1813D21}",
        run_every_n_seconds=300,
        is_read_only=True,
        unattended_mode_pref="umpOptional",
        personal_data_pref="pdpNotNeeded",
    )
    root = ET.fromstring(qwc)
    assert root.tag == "QBWCXML"
    assert root.findtext("OwnerID").startswith("{57F3B9B0")
    assert root.findtext("QBType") == "QBFS"
    assert root.findtext("AuthFlags") == "0x0"
    assert root.findtext("IsReadOnly") == "true"
    assert root.findtext("UnattendedModePref") == "umpOptional"
    assert root.findtext("PersonalDataPref") == "pdpNotNeeded"
    assert root.find("Scheduler").findtext("RunEveryNSeconds") == "300"


def test_qwc_rejects_invalid_auth_flags():
    with pytest.raises(ValueError, match="auth_flags"):
        build_qwc(
            app_name="Books Bridge",
            app_id="",
            app_url="https://books.example.com/qbwc",
            app_description="Read only",
            username="qbwc",
            owner_id="{57F3B9B0-86F1-4fcc-B1EE-566DE1813D20}",
            file_id="{57F3B9B0-86F1-4fcc-B1EE-566DE1813D21}",
            auth_flags=16,
        )
