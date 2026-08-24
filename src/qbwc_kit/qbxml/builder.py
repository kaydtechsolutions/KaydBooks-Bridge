"""qbXML request construction.

QuickBooks Desktop only accepts a very particular document: a ``?qbxml``
processing instruction, a ``QBXML`` root, and a ``QBXMLMsgsRq`` element whose
``onError`` attribute decides whether the whole batch aborts on the first bad
request. Getting any of that wrong produces an unhelpful parse error from
QuickBooks, so it is worth building rather than templating by hand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .types import OnError, iterator_supported

_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
)


def escape(value: Any) -> str:
    text = "" if value is None else str(value)
    for char, replacement in _ESCAPES:
        text = text.replace(char, replacement)
    return text


def element(name: str, value: Any) -> str:
    """One leaf element. ``None`` renders nothing so optional fields can be passed through."""
    if value is None:
        return ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"<{name}>{escape(value)}</{name}>"


def elements(fields: Mapping[str, Any] | Sequence[tuple[str, Any]]) -> str:
    """Render an ordered mapping of leaf elements.

    qbXML is order-sensitive: the schema is a sequence, not a set. Python dicts
    preserve insertion order, which is exactly the guarantee this relies on.
    """
    items = fields.items() if isinstance(fields, Mapping) else fields
    return "".join(element(name, value) for name, value in items)


def ref(name: str, full_name: str | None = None, list_id: str | None = None) -> str:
    """A ``*Ref`` aggregate. QuickBooks accepts either a ListID or a FullName."""
    if full_name is None and list_id is None:
        return ""
    body = element("ListID", list_id) + element("FullName", full_name)
    return f"<{name}>{body}</{name}>"


@dataclass
class Request:
    """A single ``*Rq`` element inside the batch."""

    name: str
    body: str = ""
    request_id: str | None = None
    iterator: str | None = None
    iterator_id: str | None = None
    max_returned: int | None = None

    def render(self) -> str:
        attrs = ""
        if self.request_id is not None:
            attrs += f' requestID="{escape(self.request_id)}"'
        if self.iterator is not None:
            if not iterator_supported(self.name):
                raise ValueError(f"{self.name} does not support iterators")
            attrs += f' iterator="{escape(self.iterator)}"'
        if self.iterator_id is not None:
            attrs += f' iteratorID="{escape(self.iterator_id)}"'

        body = self.body
        if self.max_returned is not None:
            # MaxReturned must lead the query body per the schema sequence.
            body = element("MaxReturned", self.max_returned) + body

        return f"<{self.name}{attrs}>{body}</{self.name}>"


@dataclass
class QBXMLRequest:
    """A qbXML batch, ready to hand back from ``sendRequestXML``."""

    requests: list[Request] = field(default_factory=list)
    on_error: OnError = OnError.STOP
    version: str = "13.0"

    def add(self, request: Request) -> QBXMLRequest:
        self.requests.append(request)
        return self

    def extend(self, requests: Iterable[Request]) -> QBXMLRequest:
        self.requests.extend(requests)
        return self

    def render(self) -> str:
        if not self.requests:
            raise ValueError("a qbXML batch needs at least one request")
        body = "".join(request.render() for request in self.requests)
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<?qbxml version="{escape(self.version)}"?>'
            "<QBXML>"
            f'<QBXMLMsgsRq onError="{self.on_error.value}">'
            f"{body}"
            "</QBXMLMsgsRq>"
            "</QBXML>"
        )

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.render()


def query(
    entity: str,
    *,
    request_id: str | None = None,
    max_returned: int | None = None,
    iterator: str | None = None,
    iterator_id: str | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
    active_status: str | None = None,
    include_fields: Sequence[str] | None = None,
    owner_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Request:
    """Build a ``<Entity>QueryRq``.

    ``modified_after`` is the workhorse for incremental syncs: pair it with the
    last successful sync timestamp and QuickBooks returns only what changed.
    """
    body = ""
    if modified_after is not None or modified_before is not None:
        body += (
            "<ModifiedDateRangeFilter>"
            + element("FromModifiedDate", modified_after)
            + element("ToModifiedDate", modified_before)
            + "</ModifiedDateRangeFilter>"
        )
    body += element("ActiveStatus", active_status)
    if extra:
        body += elements(extra)
    if owner_id is not None:
        body += element("OwnerID", owner_id)
    if include_fields:
        body += "".join(element("IncludeRetElement", name) for name in include_fields)

    return Request(
        name=f"{entity}QueryRq",
        body=body,
        request_id=request_id,
        iterator=iterator,
        iterator_id=iterator_id,
        max_returned=max_returned,
    )


def add(entity: str, fields: Mapping[str, Any], *, request_id: str | None = None) -> Request:
    """Build an ``<Entity>AddRq`` wrapping an ``<Entity>Add`` aggregate."""
    inner = elements(fields) if isinstance(fields, Mapping) else str(fields)
    return Request(
        name=f"{entity}AddRq",
        body=f"<{entity}Add>{inner}</{entity}Add>",
        request_id=request_id,
    )


def mod(
    entity: str,
    fields: Mapping[str, Any],
    *,
    list_id: str | None = None,
    txn_id: str | None = None,
    edit_sequence: str,
    request_id: str | None = None,
) -> Request:
    """Build an ``<Entity>ModRq``.

    QuickBooks uses optimistic concurrency: every modification must carry the
    ``EditSequence`` returned by the last read, and a stale one is rejected
    rather than silently overwriting somebody else's edit.
    """
    if list_id is None and txn_id is None:
        raise ValueError("a Mod request needs either a ListID or a TxnID")
    head = element("ListID", list_id) + element("TxnID", txn_id)
    head += element("EditSequence", edit_sequence)
    return Request(
        name=f"{entity}ModRq",
        body=f"<{entity}Mod>{head}{elements(fields)}</{entity}Mod>",
        request_id=request_id,
    )
