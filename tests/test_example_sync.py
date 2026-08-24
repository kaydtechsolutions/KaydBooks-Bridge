"""Runs the shipped example end to end against the fake QuickBooks.

An example that has drifted from the library is worse than no example, so it
gets the same treatment as the rest of the code.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import sync_to_sqlite as example  # noqa: E402
from qbwc_kit.service import QBWCService  # noqa: E402
from qbwc_kit.session import StaticAuthenticator  # noqa: E402
from qbwc_kit.testing import FakeQuickBooks, FakeWebConnector, service_transport  # noqa: E402

CUSTOMERS = [
    {"ListID": f"8000000{i}-1", "EditSequence": "1", "Name": f"Customer {i}", "Balance": "0.00"}
    for i in range(7)
]
INVOICES = [
    {
        "TxnID": f"A{i}-1",
        "RefNumber": str(1000 + i),
        "CustomerRef": {"ListID": "80000001-1", "FullName": "Customer 1"},
        "Subtotal": "125.00",
        "TxnDate": "2026-03-01",
    }
    for i in range(3)
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(example, "DB_PATH", tmp_path / "mirror.db")
    return example.DB_PATH


def run(tasks, quickbooks):
    service = QBWCService(authenticator=StaticAuthenticator("qbwc", "pw", tasks))
    connector = FakeWebConnector(
        transport=service_transport(service), username="qbwc", password="pw"
    )
    return connector.run_update(quickbooks)


def rows(db, table):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()


def test_customers_and_invoices_are_mirrored(db):
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS, "Invoice": INVOICES})
    result = run([example.MirrorCustomers(), example.MirrorInvoices()], quickbooks)

    assert result.progress[-1] == 100
    assert len(rows(db, "customer")) == len(CUSTOMERS)
    assert len(rows(db, "invoice")) == len(INVOICES)


def test_pagination_is_driven_by_page_size(db):
    task = example.MirrorCustomers()
    task.page_size = 2
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS})
    run([task], quickbooks)

    assert len(quickbooks.seen) == 4  # ceil(7 / 2)
    assert len(rows(db, "customer")) == 7


def test_second_run_asks_only_for_what_changed(db):
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS})
    run([example.MirrorCustomers()], quickbooks)
    assert "ModifiedDateRangeFilter" not in quickbooks.seen[0]

    quickbooks.seen.clear()
    run([example.MirrorCustomers()], quickbooks)
    assert "FromModifiedDate" in quickbooks.seen[0]


def test_repeated_syncs_upsert_rather_than_duplicate(db):
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS})
    for _ in range(3):
        run([example.MirrorCustomers()], quickbooks)
    assert len(rows(db, "customer")) == len(CUSTOMERS)


def test_watermark_is_not_advanced_when_quickbooks_refuses(db):
    quickbooks = FakeQuickBooks(entities={"Customer": CUSTOMERS}, supported=set())
    result = run([example.MirrorCustomers()], quickbooks)

    assert result.failed is False  # the task handled it rather than aborting
    assert example.last_success("customers") is None
    assert rows(db, "customer") == []


def test_empty_company_file_is_not_a_failure(db):
    quickbooks = FakeQuickBooks(entities={})
    result = run([example.MirrorCustomers()], quickbooks)
    assert result.progress[-1] == 100
    assert rows(db, "customer") == []
    assert example.last_success("customers") is not None
