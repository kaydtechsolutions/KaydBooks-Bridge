import pytest

from qbwc_kit import qbxml
from qbwc_kit.qbxml import QBXMLRequest
from qbwc_kit.session import (
    Session,
    SessionError,
    SessionStore,
    SimpleTask,
    StaticAuthenticator,
    UnknownTicket,
)

EMPTY = '<QBXML><QBXMLMsgsRs><CustomerQueryRs statusCode="1"/></QBXMLMsgsRs></QBXML>'


class RecordingTask:
    """Yields a fixed number of requests and records what came back."""

    def __init__(self, name, count=1):
        self.name = name
        self.count = count
        self.seen = []

    def run(self, ctx):
        for i in range(self.count):
            response = yield QBXMLRequest([qbxml.query("Customer", request_id=f"{self.name}-{i}")])
            self.seen.append(response)


def make_session(tasks):
    return Session(ticket="t", username="u", tasks=list(tasks), created_at=0.0)


def test_single_task_single_round_trip():
    task = RecordingTask("a")
    session = make_session([task])

    request = session.next_request()
    assert "CustomerQueryRq" in request
    session.submit_response(EMPTY)

    assert session.next_request() == ""
    assert session.finished
    assert len(task.seen) == 1


def test_multi_round_trip_task_resumes_where_it_left_off():
    task = RecordingTask("a", count=3)
    session = make_session([task])

    for expected in ("a-0", "a-1", "a-2"):
        assert expected in session.next_request()
        session.submit_response(EMPTY)

    assert session.next_request() == ""
    assert len(task.seen) == 3


def test_tasks_run_in_order():
    order = []

    class Ordered(RecordingTask):
        def run(self, ctx):
            order.append(self.name)
            yield from super().run(ctx)

    session = make_session([Ordered("first"), Ordered("second")])
    while session.next_request():
        session.submit_response(EMPTY)

    assert order == ["first", "second"]


def test_progress_never_reaches_100_early():
    session = make_session([RecordingTask("a"), RecordingTask("b")])

    session.next_request()
    session.submit_response(EMPTY)
    assert session.progress() == 50

    session.next_request()
    session.submit_response(EMPTY)
    assert session.progress() == 100


def test_progress_is_capped_at_99_with_many_tasks():
    # 200 tasks would round to 100 partway through and end the session early.
    session = make_session([RecordingTask(str(i)) for i in range(200)])
    for _ in range(199):
        session.next_request()
        session.submit_response(EMPTY)
    assert session.progress() == 99


def test_task_that_yields_nothing_is_skipped():
    class NoOp:
        name = "noop"

        def run(self, ctx):
            return
            yield  # pragma: no cover - makes this a generator

    task = RecordingTask("real")
    session = make_session([NoOp(), task])
    assert "CustomerQueryRq" in session.next_request()
    session.submit_response(EMPTY)
    assert session.finished


def test_pagination_loop_reads_every_page():
    class Paged:
        name = "paged"

        def __init__(self):
            self.pages = 0

        def run(self, ctx):
            request = qbxml.query("Customer", max_returned=2, iterator="Start")
            while True:
                result = yield QBXMLRequest([request])
                page = result.first()
                self.pages += 1
                if not page.has_more:
                    return
                request.iterator = "Continue"
                request.iterator_id = page.iterator_id

    task = Paged()
    session = make_session([task])

    remaining = [4, 2, 0]
    for expected in remaining:
        request = session.next_request()
        assert request
        session.submit_response(
            f'<QBXML><QBXMLMsgsRs><CustomerQueryRs statusCode="0" '
            f'iteratorRemainingCount="{expected}" iteratorID="i"/></QBXMLMsgsRs></QBXML>'
        )

    assert session.next_request() == ""
    assert task.pages == 3


def test_cannot_request_twice_without_responding():
    session = make_session([RecordingTask("a")])
    session.next_request()
    with pytest.raises(SessionError):
        session.next_request()


def test_cannot_respond_without_an_outstanding_request():
    session = make_session([RecordingTask("a")])
    with pytest.raises(SessionError):
        session.submit_response(EMPTY)


def test_abort_records_the_error_and_moves_on():
    first, second = RecordingTask("a", count=5), RecordingTask("b")
    session = make_session([first, second])
    session.next_request()
    session.abort("company file locked")

    assert session.last_error() == "company file locked"
    assert "CustomerQueryRq" in session.next_request()
    assert session.index == 1


def test_simple_task_helper():
    class Sync(SimpleTask):
        name = "sync"

        def __init__(self):
            super().__init__()
            self.records = None

        def build(self, ctx):
            return QBXMLRequest([qbxml.query("Customer")])

        def handle(self, ctx, response):
            self.records = response.first().records

    task = Sync()
    session = make_session([task])
    session.next_request()
    session.submit_response(
        '<QBXML><QBXMLMsgsRs><CustomerQueryRs statusCode="0">'
        "<CustomerRet><ListID>1</ListID></CustomerRet></CustomerQueryRs></QBXMLMsgsRs></QBXML>"
    )
    assert task.records == [{"ListID": "1"}]


def test_task_can_yield_a_raw_string():
    class Raw:
        name = "raw"

        def run(self, ctx):
            yield "<QBXML/>"

    session = make_session([Raw()])
    assert session.next_request() == "<QBXML/>"


def test_context_exposes_negotiated_version():
    session = make_session([])
    session.context.major_version, session.context.minor_version = 13, 0
    assert session.context.qbxml_version == "13.0"


class TestSessionStore:
    def test_tickets_are_unique_and_opaque(self):
        store = SessionStore()
        first = store.create("u", [])
        second = store.create("u", [])
        assert first.ticket != second.ticket
        assert len(first.ticket) >= 20

    def test_get_and_close(self):
        store = SessionStore()
        session = store.create("u", [])
        assert store.get(session.ticket) is session
        assert store.close(session.ticket) is session
        assert session.closed
        with pytest.raises(UnknownTicket):
            store.get(session.ticket)

    def test_closing_an_unknown_ticket_is_not_an_error(self):
        assert SessionStore().close("nope") is None

    def test_stale_sessions_are_pruned(self):
        store = SessionStore(ttl_seconds=0.0)
        store.create("u", [])
        assert store.prune() == 1
        assert len(store) == 0


class TestStaticAuthenticator:
    def test_accepts_matching_credentials(self):
        auth = StaticAuthenticator("qbwc", "s3cret", [])
        assert auth.authenticate("qbwc", "s3cret")

    @pytest.mark.parametrize(
        "user,password", [("qbwc", "wrong"), ("nope", "s3cret"), ("", ""), ("qbwc", "")]
    )
    def test_rejects_anything_else(self, user, password):
        assert not StaticAuthenticator("qbwc", "s3cret", []).authenticate(user, password)

    def test_hands_out_a_fresh_task_list(self):
        tasks = [RecordingTask("a")]
        auth = StaticAuthenticator("u", "p", tasks)
        first = auth.tasks_for("u")
        first.clear()
        assert len(auth.tasks_for("u")) == 1
