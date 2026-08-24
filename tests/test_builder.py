from xml.etree import ElementTree as ET

import pytest

from qbwc_kit import qbxml
from qbwc_kit.qbxml import OnError, QBXMLRequest, Request


def test_document_has_qbxml_processing_instruction():
    xml = QBXMLRequest([qbxml.query("Customer")], version="13.0").render()
    assert xml.startswith('<?xml version="1.0" encoding="utf-8"?><?qbxml version="13.0"?>')


def test_on_error_attribute():
    xml = QBXMLRequest([qbxml.query("Customer")], on_error=OnError.CONTINUE).render()
    assert 'onError="continueOnError"' in xml


def test_empty_batch_is_rejected():
    with pytest.raises(ValueError):
        QBXMLRequest([]).render()


def test_max_returned_precedes_other_filters():
    xml = qbxml.query("Invoice", max_returned=50, active_status="ActiveOnly").render()
    assert xml.index("MaxReturned") < xml.index("ActiveStatus")


def test_modified_date_range_filter():
    xml = qbxml.query("Invoice", modified_after="2026-01-01T00:00:00").render()
    assert "<ModifiedDateRangeFilter><FromModifiedDate>2026-01-01T00:00:00" in xml


def test_iterator_attributes():
    xml = qbxml.query("Customer", iterator="Continue", iterator_id="abc").render()
    assert 'iterator="Continue"' in xml
    assert 'iteratorID="abc"' in xml


def test_iterator_on_unsupported_entity_fails_at_build_time():
    # QuickBooks answers this with an opaque parse error, so it is caught here.
    with pytest.raises(ValueError, match="iterator"):
        qbxml.query("Company", iterator="Start").render()


def test_include_ret_elements_trim_the_response():
    xml = qbxml.query("Customer", include_fields=["ListID", "Name"]).render()
    assert xml.count("<IncludeRetElement>") == 2


def test_add_wraps_the_aggregate():
    xml = qbxml.add("Customer", {"Name": "Acme", "IsActive": True}).render()
    assert "<CustomerAddRq><CustomerAdd><Name>Acme</Name><IsActive>true</IsActive>" in xml


def test_none_valued_fields_are_omitted():
    xml = qbxml.add("Customer", {"Name": "Acme", "Phone": None}).render()
    assert "Phone" not in xml


def test_field_order_is_preserved():
    fields = {"Name": "A", "CompanyName": "B", "Phone": "C"}
    xml = qbxml.add("Customer", fields).render()
    assert xml.index("Name") < xml.index("CompanyName") < xml.index("Phone")


def test_mod_requires_an_identifier():
    with pytest.raises(ValueError):
        qbxml.mod("Customer", {"Name": "x"}, edit_sequence="1")


def test_mod_carries_edit_sequence_for_optimistic_concurrency():
    xml = qbxml.mod("Customer", {"Name": "x"}, list_id="80000001-1", edit_sequence="17").render()
    assert "<ListID>80000001-1</ListID><EditSequence>17</EditSequence>" in xml


def test_escaping_of_ampersands_and_angle_brackets():
    xml = qbxml.add("Customer", {"Name": "Smith & Sons <NJ>"}).render()
    assert "Smith &amp; Sons &lt;NJ&gt;" in xml
    # And the result is still well-formed.
    ET.fromstring(xml.split("?>")[-1])


def test_ref_accepts_either_key():
    assert (
        qbxml.ref("CustomerRef", full_name="Acme")
        == "<CustomerRef><FullName>Acme</FullName></CustomerRef>"
    )
    assert qbxml.ref("CustomerRef", list_id="1") == "<CustomerRef><ListID>1</ListID></CustomerRef>"
    assert qbxml.ref("CustomerRef") == ""


def test_request_id_round_trips():
    xml = QBXMLRequest([Request("CustomerQueryRq", request_id="q1")]).render()
    assert 'requestID="q1"' in xml


def test_rendered_batch_is_well_formed_xml():
    batch = QBXMLRequest(
        [
            qbxml.query("Customer", max_returned=10, request_id="1"),
            qbxml.add("Vendor", {"Name": "Supplier"}, request_id="2"),
        ]
    )
    root = ET.fromstring(batch.render().split("?>")[-1])
    assert root.tag == "QBXML"
    assert len(root.find("QBXMLMsgsRq")) == 2
