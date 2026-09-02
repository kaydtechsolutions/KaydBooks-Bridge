"""qbwc-kit: talk to QuickBooks Desktop over the Web Connector.

QuickBooks Desktop has no HTTP API. The only supported way in is the Web
Connector: a Windows service that polls *your* SOAP endpoint on a schedule,
asks it for qbXML, hands that to QuickBooks over COM, and posts the response
back. Every integration therefore has to implement the same eight callbacks and
the same request/response loop before it can read a single invoice.

This package is that plumbing:

* :mod:`qbwc_kit.soap` - the small SOAP slice QBWC actually uses
* :mod:`qbwc_kit.qbxml` - qbXML request building and status-aware parsing
* :mod:`qbwc_kit.session` - generator-based tasks spanning many round trips
* :mod:`qbwc_kit.service` - the eight callbacks, framework-agnostic
* :mod:`qbwc_kit.server` - optional FastAPI adapter and WSDL hosting
* :mod:`qbwc_kit.testing` - a fake Web Connector and a fake QuickBooks

The core has no dependencies outside the standard library.
"""

from . import qbxml
from .qbxml import (
    QBXMLRequest,
    QBXMLStatusError,
    Request,
    Response,
    ResponseSet,
    add,
    mod,
    parse_response,
    query,
)
from .service import QBWCService
from .session import (
    Authenticator,
    Session,
    SessionStore,
    SimpleTask,
    StaticAuthenticator,
    Task,
    TaskContext,
)
from .wsdl import build_qwc, build_wsdl

__version__ = "0.1.2"

__all__ = [
    "Authenticator",
    "QBWCService",
    "QBXMLRequest",
    "QBXMLStatusError",
    "Request",
    "Response",
    "ResponseSet",
    "Session",
    "SessionStore",
    "SimpleTask",
    "StaticAuthenticator",
    "Task",
    "TaskContext",
    "__version__",
    "add",
    "build_qwc",
    "build_wsdl",
    "mod",
    "parse_response",
    "qbxml",
    "query",
]
