"""Shared qbXML vocabulary."""

from __future__ import annotations

from enum import Enum


class OnError(str, Enum):
    """``QBXMLMsgsRq/@onError``.

    ``STOP`` aborts the batch at the first failing request. ``CONTINUE`` runs
    every request and reports per-request status, which is what you want for a
    read-only sync where one unsupported entity should not blank the rest.
    """

    STOP = "stopOnError"
    CONTINUE = "continueOnError"


class Severity(str, Enum):
    INFO = "Info"
    WARN = "Warn"
    ERROR = "Error"


#: Status codes worth branching on. QuickBooks defines several hundred; these
#: are the ones that change control flow rather than just being logged.
STATUS_OK = 0
STATUS_NOTHING_FOUND = 1
STATUS_UNSUPPORTED_REQUEST = 3100
STATUS_INSUFFICIENT_PERMISSION = 3260
STATUS_STALE_EDIT_SEQUENCE = 3200
STATUS_OBJECT_NOT_FOUND = 500

#: Entities whose Query requests accept ``iterator``/``iteratorID`` attributes.
#: Asking for an iterator on anything else is a parse error from QuickBooks,
#: so it is checked at build time instead.
ITERATOR_ENTITIES = frozenset(
    {
        "AccountQueryRq",
        "BillQueryRq",
        "CheckQueryRq",
        "CreditMemoQueryRq",
        "CustomerQueryRq",
        "DepositQueryRq",
        "EstimateQueryRq",
        "InvoiceQueryRq",
        "ItemInventoryQueryRq",
        "ItemQueryRq",
        "ItemReceiptQueryRq",
        "JournalEntryQueryRq",
        "PurchaseOrderQueryRq",
        "ReceivePaymentQueryRq",
        "SalesOrderQueryRq",
        "SalesReceiptQueryRq",
        "TimeTrackingQueryRq",
        "VendorQueryRq",
    }
)


def iterator_supported(request_name: str) -> bool:
    return request_name in ITERATOR_ENTITIES
