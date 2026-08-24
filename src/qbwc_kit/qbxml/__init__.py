"""qbXML request building and response parsing."""

from .builder import QBXMLRequest, Request, add, element, elements, mod, query, ref
from .parser import (
    QBXMLParseError,
    QBXMLStatusError,
    Response,
    ResponseSet,
    parse_response,
)
from .types import (
    ITERATOR_ENTITIES,
    STATUS_NOTHING_FOUND,
    STATUS_OK,
    STATUS_UNSUPPORTED_REQUEST,
    OnError,
    Severity,
)

__all__ = [
    "ITERATOR_ENTITIES",
    "OnError",
    "QBXMLParseError",
    "QBXMLRequest",
    "QBXMLStatusError",
    "Request",
    "Response",
    "ResponseSet",
    "STATUS_NOTHING_FOUND",
    "STATUS_OK",
    "STATUS_UNSUPPORTED_REQUEST",
    "Severity",
    "add",
    "element",
    "elements",
    "mod",
    "parse_response",
    "query",
    "ref",
]
