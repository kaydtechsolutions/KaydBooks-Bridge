"""A complete, runnable QBWC integration: mirror customers and invoices into SQLite.

Run it:

    pip install 'qbwc-kit[server]' uvicorn
    python examples/sync_to_sqlite.py

Then point the Web Connector at it by importing the ``.qwc`` file printed on
startup. Nothing here needs QuickBooks to be present in order to *start* - the
service is idle until the connector calls in.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qbwc_kit import QBWCService, StaticAuthenticator, qbxml
from qbwc_kit.qbxml import QBXMLRequest
from qbwc_kit.server import create_app
from qbwc_kit.wsdl import build_qwc

DB_PATH = Path("quickbooks_mirror.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer (
    list_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    edit_sequence TEXT,
    balance TEXT,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice (
    txn_id TEXT PRIMARY KEY,
    ref_number TEXT,
    customer TEXT,
    total TEXT,
    txn_date TEXT,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    entity TEXT PRIMARY KEY,
    last_success TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def last_success(entity: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT last_success FROM sync_state WHERE entity = ?", (entity,)
        ).fetchone()
    return row[0] if row else None


def mark_success(entity: str, when: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sync_state (entity, last_success) VALUES (?, ?) "
            "ON CONFLICT(entity) DO UPDATE SET last_success = excluded.last_success",
            (entity, when),
        )


class MirrorCustomers:
    """Incremental customer sync.

    Two details make this safe to run on a schedule forever:

    * The watermark only advances after every page has been written, so an
      interrupted run repeats work instead of skipping it.
    * The watermark is rewound by a minute. QuickBooks stamps TimeModified from
      the workstation clock, and a record saved during the sync can otherwise
      land just behind the watermark and never be picked up.
    """

    name = "customers"
    page_size = 100
    overlap = timedelta(minutes=1)

    def run(self, ctx):
        started = datetime.now(timezone.utc)
        since = last_success(self.name)
        request = qbxml.query(
            "Customer",
            max_returned=self.page_size,
            iterator="Start",
            modified_after=since,
        )

        total = 0
        while True:
            result = yield QBXMLRequest([request])
            page = result.first()
            if not page.ok:
                ctx.log(f"customer sync refused by QuickBooks: {page.status_message}")
                ctx.session.record_error(page.status_message)
                return

            total += self._write(page.records)
            if not page.has_more:
                break
            request.iterator = "Continue"
            request.iterator_id = page.iterator_id

        watermark = (started - self.overlap).strftime("%Y-%m-%dT%H:%M:%S")
        mark_success(self.name, watermark)
        ctx.log(f"mirrored {total} customer(s) since {since or 'the beginning'}")

    @staticmethod
    def _write(records) -> int:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                record.get("ListID"),
                record.get("Name", ""),
                record.get("EditSequence"),
                record.get("Balance"),
                now,
            )
            for record in records
        ]
        with connect() as conn:
            conn.executemany(
                "INSERT INTO customer (list_id, name, edit_sequence, balance, synced_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(list_id) DO UPDATE SET "
                "name = excluded.name, edit_sequence = excluded.edit_sequence, "
                "balance = excluded.balance, synced_at = excluded.synced_at",
                rows,
            )
        return len(rows)


class MirrorInvoices:
    """Same shape as the customer sync, with line items left out of the mirror."""

    name = "invoices"
    page_size = 50

    def run(self, ctx):
        started = datetime.now(timezone.utc)
        since = last_success(self.name)
        request = qbxml.query(
            "Invoice",
            max_returned=self.page_size,
            iterator="Start",
            modified_after=since,
            extra={"IncludeLineItems": False},
        )

        total = 0
        while True:
            result = yield QBXMLRequest([request])
            page = result.first()
            if not page.ok:
                ctx.session.record_error(page.status_message)
                return

            now = datetime.now(timezone.utc).isoformat()
            rows = [
                (
                    record.get("TxnID"),
                    record.get("RefNumber"),
                    (record.get("CustomerRef") or {}).get("FullName"),
                    record.get("Subtotal"),
                    record.get("TxnDate"),
                    now,
                )
                for record in page.records
            ]
            with connect() as conn:
                conn.executemany(
                    "INSERT INTO invoice (txn_id, ref_number, customer, total, txn_date, synced_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(txn_id) DO UPDATE SET "
                    "ref_number = excluded.ref_number, customer = excluded.customer, "
                    "total = excluded.total, txn_date = excluded.txn_date, "
                    "synced_at = excluded.synced_at",
                    rows,
                )
            total += len(rows)

            if not page.has_more:
                break
            request.iterator = "Continue"
            request.iterator_id = page.iterator_id

        mark_success(self.name, (started - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S"))
        ctx.log(f"mirrored {total} invoice(s)")


def report(session) -> None:
    for message in session.messages:
        logging.info("%s", message)
    for error in session.errors:
        logging.error("%s", error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000/qbwc", help="public endpoint URL")
    parser.add_argument("--user", default="qbwc")
    parser.add_argument("--password", default="change-me")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--write-qwc", type=Path, help="write the .qwc file and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    if args.write_qwc:
        # OwnerID and FileID identify this integration to QuickBooks. They are
        # generated once and then kept: changing them makes every user
        # re-authorise the app.
        qwc = build_qwc(
            app_name="qbwc-kit SQLite mirror",
            app_id="",
            app_url=args.url,
            app_description="Mirrors customers and invoices into SQLite",
            username=args.user,
            owner_id="{" + str(uuid.uuid4()).upper() + "}",
            file_id="{" + str(uuid.uuid4()).upper() + "}",
            run_every_n_seconds=900,
        )
        args.write_qwc.write_text(qwc)
        print(f"wrote {args.write_qwc} - import this in the Web Connector")
        return

    service = QBWCService(
        authenticator=StaticAuthenticator(
            args.user, args.password, [MirrorCustomers(), MirrorInvoices()]
        ),
        on_session_end=report,
    )
    app = create_app(service, endpoint_url=args.url)

    import uvicorn

    logging.info(
        "serving WSDL at %s - run with --write-qwc to generate the connector file", args.url
    )
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
