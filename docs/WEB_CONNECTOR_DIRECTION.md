# Required client connection: QuickBooks Web Connector

The operator selected QuickBooks Web Connector (`.qwc`) as the project's primary
client connection. This supersedes the direct-SDK-first deployment direction.
It records the required architecture, not a completed transaction transport migration.

## Client setup and communication

Clients install QuickBooks Desktop and QuickBooks Web Connector on Windows, import
the company-specific Bridge `.qwc` file, supply the connector credential and approve
company access. The intended normal setup does not require a separately installed
developer SDK or the Bridge's direct-SDK helper/interop assembly.

Web Connector initiates authenticated HTTPS SOAP callbacks to the Bridge's `/qbwc`
endpoint. The Bridge supplies authorized qbXML requests; Web Connector runs them
against QuickBooks and returns the responses. Updates occur when Web Connector runs,
manually or on its configured schedule. An incoming WhatsApp request is not an
immediate push into QuickBooks.

Hermes on Linux communicates with the Bridge service; it does not open the Windows
company file. Linux hosting of the complete Bridge workflow and remote Hermes
authentication still require qualification. Company paths, names, credentials,
stable OwnerID/FileID registration values and mappings remain private. A `.qwc` file
is a connection configuration, not the Bridge installer or a credential container.

## Existing implementation and migration requirements

Existing Bridge code generates stable `.qwc` files and serves durable authenticated
callbacks. Company identity, account, invoice-master and receipt read paths have
Web Connector support. The current deployment profile generator is restricted to
read-only qualification. Registration/AppLock permissions require the documented
[M2 handling](M2_QUALIFICATION.md#verified-repair-and-restart-qualification), not
repeated bootstrap imports or arbitrary replacement identifiers.

The separately qualified native accounting adapters are direct-SDK implementations.
They do not become Web Connector implementations by importing a `.qwc` file. Before
advertising `.qwc` transaction support, complete these requirements:

- Bind reviewed jobs, current permissions, company identity, pause controls and
  bounded posting authority to durable Web Connector dispatch attempts.
- Port the required fresh checks, transaction writes, independent saved-record
  verification and report queries through the callback lifecycle.
- Retain request/response evidence and handle disconnects, duplicate callbacks,
  restarts and uncertain writes without automatic resending or double posting.
- Expose queued/waiting-for-Web-Connector states honestly in browser/Hermes workflows.
- Qualify each transaction/report contract in the authorized sample company using
  Web Connector, including permission failures and interruption/recovery.
- Complete stable registration, permissions and installer/setup checks for this
  connection before client release. Do not silently fall back to direct-SDK posting.

The requested data-entry scope is invoices, sales receipts, bills, inventory
transfers, customer credit memos, supplier bill credits, account transfers, customer
and supplier payments, standalone checks, journals, item creation, chart-of-accounts
creation and a batch-entry workflow matching the operator's QuickBooks Desktop
Company-menu meaning. The last requirement is not satisfied by CSV/XLSX import alone.
Web Connector does not itself provide the native Batch Enter Transactions screen;
the Bridge batch workflow and supported underlying transaction contracts need design
and qualification. Tax remains excluded.

The initial [durable invoice posting and recovery implementation](QBWC_INVOICE_POSTING.md)
now includes browser master checks, bounded queueing, single write handoff and
independent readback. Automated callback tests pass; one installed USD5 service
invoice passed preflight, single write handoff and exact saved-invoice readback.
Duplicate dispatch was refused and posting was paused again. Actual service-invoice
interruption/recovery also passed: after a deliberate process exit before accepting
the successful add response, read-only recovery verified the same saved invoice
with one total write handoff. An installed mixed inventory/service invoice also
verified both saved lines and stock quantity 2 -> 0, with one write handoff.
Other inventory variants, contracts, reports and scheduling need migration.
No native-only gate is relabeled Web Connector-qualified. Production remains disabled.
