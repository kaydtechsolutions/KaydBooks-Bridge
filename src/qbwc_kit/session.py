"""The Web Connector session state machine.

QBWC drives the conversation. It polls on a schedule, authenticates, then asks
``sendRequestXML`` / ``receiveResponseXML`` in a loop until the server hands
back an empty request. Everything the integration wants to do has to be
expressed inside that loop, which is awkward when a single logical job needs
several round trips (query, look at the result, decide what to write next).

Tasks here are generators, so a multi-step job reads top to bottom:

.. code-block:: python

    class SyncCustomers(Task):
        name = "customers"

        def run(self, ctx):
            request = qbxml.query("Customer", max_returned=100, iterator="Start")
            while True:
                result = yield QBXMLRequest([request])
                page = result.first().raise_for_status()
                ctx.store(page.records)
                if not page.has_more:
                    return
                request.iterator = "Continue"
                request.iterator_id = page.iterator_id

The generator is suspended between HTTP round trips, so pagination, retries and
conditional writes stay in ordinary control flow instead of being flattened
into a per-request switch statement.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .qbxml import QBXMLRequest, ResponseSet, parse_response

#: What ``authenticate`` returns in slot 1 to say "use whatever company file is
#: currently open in QuickBooks", which is the only option when QuickBooks is
#: running in the foreground.
CURRENT_COMPANY_FILE = ""
#: Slot 1 value meaning "authenticated, but there is no work right now".
NO_WORK = "none"
#: Slot 1 value meaning "credentials rejected".
INVALID_USER = "nvu"


class SessionError(RuntimeError):
    pass


class UnknownTicket(SessionError):
    pass


@dataclass
class TaskContext:
    """Handed to a task on every step.

    ``company_file`` and the qbXML version are only known once QuickBooks has
    connected, so tasks receive them here rather than at construction time.
    """

    session: Session
    company_file: str = ""
    country: str = "US"
    major_version: int = 0
    minor_version: int = 0
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def qbxml_version(self) -> str:
        return f"{self.major_version}.{self.minor_version}"

    def log(self, message: str) -> None:
        self.session.messages.append(message)


TaskRun = Generator[QBXMLRequest | str, ResponseSet, None]


@runtime_checkable
class Task(Protocol):
    """A unit of work that spans one or more QBWC round trips."""

    name: str

    def run(self, ctx: TaskContext) -> TaskRun:
        """Yield qbXML requests; receive the parsed response for each."""
        ...


class SimpleTask:
    """Convenience base class for tasks that only need one round trip."""

    name = "task"

    def __init__(self, name: str | None = None) -> None:
        if name is not None:
            self.name = name

    def build(self, ctx: TaskContext) -> QBXMLRequest | str:
        raise NotImplementedError

    def handle(self, ctx: TaskContext, response: ResponseSet) -> None:
        raise NotImplementedError

    def run(self, ctx: TaskContext) -> TaskRun:
        response = yield self.build(ctx)
        self.handle(ctx, response)


@dataclass
class Session:
    """One authenticated QBWC conversation."""

    ticket: str
    username: str
    tasks: list[Task]
    created_at: float
    index: int = 0
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    closed: bool = False
    context: TaskContext = field(init=False, repr=False)
    _generator: TaskRun | None = field(default=None, init=False, repr=False)
    _pending: str | None = field(default=None, init=False, repr=False)
    _awaiting: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.context = TaskContext(session=self)

    @property
    def current_task(self) -> Task | None:
        if self.index >= len(self.tasks):
            return None
        return self.tasks[self.index]

    @property
    def finished(self) -> bool:
        return self.index >= len(self.tasks)

    def progress(self) -> int:
        """Percent complete, as QBWC's progress bar wants it.

        Reported per completed task. 100 is how the server says "session over",
        so a session with work left must never round up to it.
        """
        if self.finished or not self.tasks:
            return 100
        return min(int(self.index * 100 / len(self.tasks)), 99)

    def next_request(self) -> str:
        """Return the next qbXML request, or ``""`` when the work is done."""
        if self._awaiting:
            raise SessionError("next_request called before the previous response arrived")

        if self._pending is not None:
            payload, self._pending = self._pending, None
            self._awaiting = True
            return payload

        while not self.finished:
            task = self.tasks[self.index]
            self._generator = task.run(self.context)
            try:
                payload = next(self._generator)
            except StopIteration:
                # A task that yields nothing is a no-op, not an error.
                self._finish_task()
                continue
            self._awaiting = True
            return _render(payload)

        return ""

    def submit_response(self, payload: str) -> ResponseSet:
        """Feed a raw qbXML response back into the suspended task."""
        if not self._awaiting or self._generator is None:
            raise SessionError("submit_response called with no request outstanding")

        self._awaiting = False
        parsed = parse_response(payload)
        try:
            next_payload = self._generator.send(parsed)
        except StopIteration:
            self._finish_task()
            return parsed

        # The task wants another round trip. Hold the rendered request until
        # QBWC comes back with sendRequestXML.
        self._pending = _render(next_payload)
        return parsed

    def abort(self, message: str) -> None:
        """Give up on the current task and move on, recording why."""
        self.record_error(message)
        if self._generator is not None:
            self._generator.close()
        self._finish_task()

    def _finish_task(self) -> None:
        self._generator = None
        self._pending = None
        self._awaiting = False
        self.index += 1

    def record_error(self, message: str) -> None:
        self.errors.append(message)

    def last_error(self) -> str:
        return self.errors[-1] if self.errors else ""


def _render(payload: QBXMLRequest | str) -> str:
    return payload.render() if isinstance(payload, QBXMLRequest) else payload


class SessionStore:
    """In-memory ticket store with a TTL.

    QBWC keeps a ticket only for the duration of one update, so anything older
    than the timeout is an abandoned session (a crashed connector, a company
    file closed mid-sync) and is safe to drop.
    """

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def create(self, username: str, tasks: Sequence[Task]) -> Session:
        self.prune()
        ticket = secrets.token_urlsafe(24)
        session = Session(
            ticket=ticket,
            username=username,
            tasks=list(tasks),
            created_at=time.monotonic(),
        )
        self._sessions[ticket] = session
        return session

    def get(self, ticket: str) -> Session:
        session = self._sessions.get(ticket)
        if session is None:
            raise UnknownTicket(ticket)
        return session

    def close(self, ticket: str) -> Session | None:
        session = self._sessions.pop(ticket, None)
        if session is not None:
            session.closed = True
        return session

    def prune(self) -> int:
        cutoff = time.monotonic() - self.ttl_seconds
        stale = [t for t, s in self._sessions.items() if s.created_at < cutoff]
        for ticket in stale:
            del self._sessions[ticket]
        return len(stale)

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, ticket: object) -> bool:
        return ticket in self._sessions


@runtime_checkable
class Authenticator(Protocol):
    """Decides whether a QBWC connection may start, and what work it gets."""

    def authenticate(self, username: str, password: str) -> bool: ...

    def tasks_for(self, username: str) -> Iterable[Task]: ...


class StaticAuthenticator:
    """Single-user authenticator with a constant task list.

    The password is compared with :func:`secrets.compare_digest` so a wrong
    password costs the same time as a right one.
    """

    def __init__(self, username: str, password: str, tasks: Sequence[Task]) -> None:
        self._username = username
        self._password = password
        self._tasks = list(tasks)

    def authenticate(self, username: str, password: str) -> bool:
        user_ok = secrets.compare_digest(username, self._username)
        pass_ok = secrets.compare_digest(password, self._password)
        return user_ok and pass_ok

    def tasks_for(self, username: str) -> list[Task]:
        return list(self._tasks)
