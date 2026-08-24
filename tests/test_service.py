"""End-to-end: a fake Web Connector driving a real service against a fake QuickBooks."""

import pytest

from qbwc_kit import qbxml, soap
from qbwc_kit.qbxml import QBXMLRequest
from qbwc_kit.service import QBWCService
from qbwc_kit.session import SessionStore, StaticAuthenticator
from qbwc_kit.testing import FakeQuickBooks, FakeWebConnector, service_transport

CUSTOMERS = [
    {"ListID": f"8000000{i}-1", "Name": f"Customer {i}", "EditSequence": "1"} for i in range(5)
]


class SyncCustomers:
    """Pages through every customer, exactly as a real sync task would."""

    name = "customers"

    def __init__(self, page_size=2):
        self.page_size = page_size
        self.collected = []
        self.pages = 0

    def run(self, ctx):
        request = qbxml.query("Customer", max_returned=self.page_size, iterator="Start")
        while True:
            result = yield QBXMLRequest([request])
            page = result.first().raise_for_status()
            self.pages += 1
            self.collected.extend(page.records)
            if not page.has_more:
                return
            request.iterator = "Continue"
            request.iterator_id = page.iterator_id


def build(tasks, **kwargs):
    service = QBWCService(
        authenticator=StaticAuthenticator("qbwc", "s3cret", tasks),
        store=SessionStore(),
        **kwargs,
    )
    return service, FakeWebConnector(
        transport=service_transport(service), username="qbwc", password="s3cret"
    )


def test_full_update_cycle_collects_every_page():
    task = SyncCustomers(page_size=2)
    _, connector = build([task])
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS})

    result = connector.run_update(quickbooks)

    assert result.authenticated
    assert result.progress[-1] == 100
    assert result.close_message == "OK"
    assert task.pages == 3
    assert [record["Name"] for record in task.collected] == [c["Name"] for c in CUSTOMERS]


def test_the_first_page_starts_an_iterator_and_later_pages_continue_it():
    _, connector = build([SyncCustomers(page_size=2)])
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS})
    connector.run_update(quickbooks)

    assert 'iterator="Start"' in quickbooks.seen[0]
    assert all('iterator="Continue"' in request for request in quickbooks.seen[1:])


def test_bad_credentials_yield_nvu_and_no_ticket():
    service, connector = build([SyncCustomers()])
    connector.password = "wrong"
    result = connector.run_update(FakeQuickBooks())

    assert not result.authenticated
    assert result.last_error == "nvu"
    assert result.round_trips == 0
    assert len(service.store) == 0


def test_no_queued_work_reports_none_without_opening_the_company_file():
    _, connector = build([])
    result = connector.run_update(FakeQuickBooks())
    assert result.last_error == "none"
    assert result.round_trips == 0


def test_old_connector_builds_are_refused():
    _, connector = build([SyncCustomers()])
    connector.client_version = "1.9.0.1"
    result = connector.run_update(FakeQuickBooks())
    assert result.last_error.startswith("E:")
    assert not result.authenticated


def test_current_connector_build_is_accepted():
    service, _ = build([])
    call = soap.parse_request(soap.build_request("clientVersion", [("strVersion", "2.3.0.36")]))
    assert service._do_clientVersion(call) == ""


def test_unsupported_entity_surfaces_as_a_task_failure_not_silent_success():
    # QuickBooks answers with a well-formed envelope carrying status 3100. A
    # parser that only looked at the XML shape would treat this as "0 rows".
    task = SyncCustomers()
    _, connector = build([task])
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS}, supported=set())

    result = connector.run_update(quickbooks)

    assert result.failed
    assert "3100" in result.last_error
    assert task.collected == []


def test_quickbooks_side_error_aborts_the_session():
    service, connector = build([SyncCustomers()])
    ticket = None

    original = connector._call

    def intercept(method, params):
        nonlocal ticket
        if method == "receiveResponseXML":
            params = [(name, value) for name, value in params if name != "hresult"]
            params.append(("hresult", "0x80040400"))
            params.append(("message", "QuickBooks found an error"))
        return original(method, params)

    connector._call = intercept
    result = connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS}))

    assert result.failed
    assert "0x80040400" in result.last_error


def test_session_is_removed_on_close():
    service, connector = build([SyncCustomers()])
    result = connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS}))
    assert result.ticket not in service.store
    assert len(service.store) == 0


def test_close_reports_error_count():
    task = SyncCustomers()
    service, connector = build([task])
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS}, supported=set())
    result = connector.run_update(quickbooks)
    # The session aborted, so QBWC still calls closeConnection.
    assert result.close_message.startswith("Completed with")


def test_on_session_end_hook_fires_once():
    calls = []
    service, connector = build([SyncCustomers()], on_session_end=calls.append)
    connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS}))
    assert len(calls) == 1
    assert calls[0].finished


def test_unknown_ticket_ends_the_session_instead_of_faulting():
    # Happens whenever the server restarts mid-update. Faulting here makes QBWC
    # retry forever against a ticket that will never come back.
    service, _ = build([SyncCustomers()])
    for method, expected in (
        ("sendRequestXML", ""),
        ("receiveResponseXML", "-1"),
        ("closeConnection", "OK"),
        ("getLastError", "session expired"),
    ):
        envelope = service.dispatch(soap.build_request(method, [("ticket", "gone")]))
        assert "Fault" not in envelope
        assert f"<{method}Result>{expected}</{method}Result>" in envelope


def test_writes_are_applied_to_quickbooks():
    class AddVendor:
        name = "add-vendor"

        def __init__(self):
            self.list_id = None

        def run(self, ctx):
            result = yield QBXMLRequest([qbxml.add("Vendor", {"Name": "Supplier Co"})])
            self.list_id = result.first().raise_for_status().records[0]["ListID"]

    task = AddVendor()
    _, connector = build([task])
    quickbooks = FakeQuickBooks()
    connector.run_update(quickbooks)

    assert task.list_id
    assert quickbooks.entities["Vendor"][0]["Name"] == "Supplier Co"


def test_multiple_tasks_report_increasing_progress():
    _, connector = build([SyncCustomers(page_size=5), SyncCustomers(page_size=5)])
    result = connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS}))
    assert result.progress == sorted(result.progress)
    assert result.progress[-1] == 100


def test_garbage_request_gets_a_client_fault_not_a_crash():
    service, _ = build([])
    envelope = service.dispatch("this is not xml")
    assert "soap:Client" in envelope
    assert "faultstring" in envelope


def test_unknown_method_is_rejected():
    service, _ = build([])
    envelope = service.dispatch(soap.build_request("dropTables", []))
    assert "unsupported method" in envelope


def test_server_version_is_reported():
    _, connector = build([])
    assert connector.server_version()


def test_runaway_task_is_caught_by_the_harness():
    class NeverEnds:
        name = "runaway"

        def run(self, ctx):
            while True:
                yield QBXMLRequest([qbxml.query("Customer")])

    _, connector = build([NeverEnds()])
    connector.max_round_trips = 10
    with pytest.raises(AssertionError, match="did not terminate"):
        connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS}))
