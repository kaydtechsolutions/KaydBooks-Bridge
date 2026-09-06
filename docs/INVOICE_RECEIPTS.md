# Invoice request and saved-receipt qualification

`invoice_receipt.add_request(policy, payload, request_id)` constructs an **unsubmitted**
qbXML 17.0 request for the narrow non-taxable service case. It is a pure helper: it does
not authenticate a caller, establish fresh evidence, open QuickBooks or send anything.
Callers must establish those controls separately before any future dispatch integration.
There is no public CLI or service action that sends this request. General posting remains
disabled; the existing native read-only allowlist continues to reject write requests.

The supported policy requires commercial checks, no tax item, zero tax, configured
single currency and service items only. Customer, receivables and item ListIDs come from
private mappings. Quantities and list prices are explicit. Pending, finance-charge, print
and email flags are false. QuickBooks computes line amounts; receipt comparison must match
the validated amounts. Taxable, explicit multicurrency and inventory modes are rejected.

`validate_receipt` accepts an InvoiceAdd or InvoiceQuery response. It requires exactly one
correlated successful InvoiceRet and compares customer, receivables account, date, reference,
flags, tax code, subtotal, tax, applied amount, remaining balance, every line's item/code,
quantity, rate and amount. It checks an expected TxnID when provided and requires distinct
line identities. Duplicate/missing fields, additional invoices or lines, groups, linked
transactions, payments, UOM and other unsupported features fail. Absence and warning/error
responses never qualify a saved invoice. XML with DTD declarations is rejected.

The validator proves only that the supplied receipt matches the intended fields. Trusted
transport provenance, company identity, dispatch ownership, durable intent and receipt
publication must be established by the caller. A client-supplied XML document cannot by
itself establish that a real transaction occurred.

## Actual sample qualification

The operator approved one sample service invoice: quantity 2, price 5.00, tax 0.00 and
total 10.00. Fresh Bridge SDK evidence and the owned validated review passed. A private
one-time helper pinned the exact request hash, held the company and native session locks,
checked company identity, active masters/accounts/code, disabled sales tax/multicurrency,
customer balance and absence of the reference before sending the sole InvoiceAdd.

Dispatch intent was persisted with exclusive creation and flushed before the native call.
The add response and returned TxnID were persisted before an independent query by TxnID.
Both passed the shared receipt validator. A new read-only session queried by reference
and returned the same matching invoice. A repeat helper invocation was blocked by the
existing intent before any SDK call. The audit chain verified. Raw XML, exact identities,
the write helper and all authorization/qualification artifacts remain outside Git.

This is a successful bounded sample test, not qualification of a production posting
adapter. The Bridge simulation job was not relabeled as a production success. No second
invoice, tax/settings change, stock movement, email, payment, deletion or void was sent.
An interrupted or uncertain write must be reconciled by reads before considering further
action; this helper has no automatic retry path, even when reconciliation finds nothing.

The implementation used Intuit's official [InvoiceAdd request schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/InvoiceAddRq.json),
[InvoiceQuery request schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/InvoiceQueryRq.json)
and [InvoiceQuery response schema](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/InvoiceQueryRs.json).

## Durable readback and shared job completion

The direct SDK command supports `--receipt-check` with a private JSON file containing
`txn_id` and the intended `payload`. It constructs HostQuery, CompanyQuery and exactly
one InvoiceQuery by TxnID with line items, linked transactions and fixed projected fields.
The native gate rejects writes, reference searches, multiple IDs and arbitrary fields.
Both `read` and `validate` permissions are required. The durable SDK run binds owner,
connector, company, request and current payload/mapping policy. Recovery reuses a saved
response; replay cannot refresh the first dispatch timestamp.

After this read succeeds, the authenticated core CLI accepts:

```text
kaydbooks-bridge --config PRIVATE_CONFIG --company COMPANY_ID attach-receipt JOB_ID PRIVATE_REFERENCE_JSON
```

The reference file contains only:

```json
{"transport":"direct-sdk","connector":"connector-company-a","id":"1234"}
```

`Bridge.attach_receipt` requires current `recover`, `read` and `validate` grants and job
ownership. It resolves the durable verified SDK response, rechecks the exact request,
payload/policy context, company identity and audit chain, and rejects stale evidence using
the configured invoice evidence TTL. Client XML, timestamps and success claims are rejected.
Only an undispatched validated or queued invoice can be attached. No QuickBooks mutation
occurs, and simulation attempts cannot be relabeled as real dispatches.

The receipt insert, transition to `verified` and audit event commit atomically. The receipt
has `origin: external-invoice-readback` and `bridge_dispatched: false`; the job records the
real TxnID with `attempt` still null. Receipts are immutable and a company TxnID can belong
to only one attached job. The SQLite migration preserves existing jobs and state guards.

An identical owned replay returns the saved receipt even after the observation expires:
that is historical completion, not a fresh observation of the current invoice. Current
permissions are still checked. Duplicate preparation returns the same completed job and
cannot reopen it, change its payload or initiate a dispatch. Status exposes the receipt
and TxnID. Changed payloads, conflicting keys, new receipt references and state regressions
fail. Further payments or edits are outside this narrow receipt qualification.

The previously approved sample invoice passed this new read-only transport and attachment
path. Restart, duplicate preparation and audit verification passed with zero new writes.
This supersedes the earlier milestone's separate-only receipt record; the job now explicitly
records verified external provenance. QBWC receipt attachment and general posting remain
unimplemented, and this code does not enable production writes.
