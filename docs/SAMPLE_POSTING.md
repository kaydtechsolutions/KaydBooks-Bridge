# Controlled sample invoice posting

Production posting remains unavailable. An optional company `sample_posting` policy enables
only explicitly authorized non-taxable service and simple inventory invoice tests through the native SDK.
It requires a confirmed connector, an operator authorization statement, a reference prefix,
an expiry timestamp and an attempt quota of 1–10. The company's normal invoice total limit,
master mappings, source checks and approval policy still apply. Each initiator needs current
`post-sample`, `submit`, `read` and `validate` grants. Recovery uses `recover/read/validate`.

Prepare and validate an invoice with fresh evidence, review it, and submit it to the queue.
The explicit `post-sample JOB_ID` command is separate from `simulate`. Before starting a
native session it checks pause, queue state, unresolved writes, active read sessions, quota,
audit integrity and current authorization. It persists an immutable attempt and exact
InvoiceAdd snapshot while atomically transitioning the shared job to in-flight.

The native helper opens a single-user session with no personal-data access. It sends fixed
company/master preflight queries and a duplicate query for the exact reference. The parent
validates the response with shared company and commercial validators, reloads policy and
permissions and checks the lease/pause before authorizing the pinned request hash. A matching
existing invoice is reconciled without writing. Any mismatch prevents write authorization.
The helper waits at most 30 seconds for the parent's authorization after preflight completes.

An exclusive durable native write intent precedes the sole InvoiceAdd. The add response is
flushed before the helper closes. Parent or helper failures never cause automatic resend.
The shared job becomes posted-unverified after a valid add receipt. A separate read-only
session checks the reference and current company/masters, followed by a durable SDK TxnID
query. Only an exact match promotes the job to verified and appends the durable audit proof.

Use `reconcile-sample JOB_ID` for unknown or posted-unverified native attempts. It sends
only queries and requires the original dispatch mapping, gate and connector context. Absence
or ambiguity remains unknown; it does not authorize retry. Expired in-flight leases move to
unknown through the existing recover action. SDK native mutexes prevent overlapping helpers,
including when an orphaned helper survives its parent. QBWC authentication is held while a
native write is in-flight or unknown. Native jobs cannot use simulated reconciliation.

Completed jobs expose the receipt from immutable audit evidence with origin
`native-attempt-readback`. `bridge_dispatched` is true only with a matching persisted add
response, false for a preflight duplicate, and null when reconciliation proves the saved
invoice but the add-response evidence is missing. This avoids claiming that a readback proves
which process created a record. Duplicate preparation retains the same completed job.

Known limits: a pause cannot retract a write already authorized to the helper. A lost or
absent result can require operator investigation, and authorization changes may need the
original private context restored for reconciliation. This is not a production enablement,
automatic retry facility, full tax/inventory adapter or exactly-once guarantee.

Real sample qualification passed for a normal native posting and a second invoice whose
parent exited after the helper saved its response. A fresh process reconciled the second
invoice and refused another dispatch. Exact receipts and authorization remain private.
Production access is outside this qualification.

Simple inventory and mixed invoices use exact private item/asset/COGS/income mappings.
Fresh preflight checks require supported inventory settings and sufficient uncommitted
stock. Advanced inventory, serial/lot tracking, multiple locations, units of measure,
FIFO and tax-inclusive items are rejected by this sample path. The authorized native
preflight stores each inventory item's starting quantity in immutable audit evidence.
Independent receipt queries must match saved lines and the exact expected stock decrease.
Missing stock proof or a different decrease keeps the job unverified and prevents resend.
Concurrent external stock activity can require investigation; the comparison does not
claim transaction isolation from other QuickBooks users or general-ledger valuation.

Recovery checks the saved invoice without requiring stock still available for another
sale. It still requires the original stock baseline and independent current quantity.
A pre-existing inventory invoice with no owned native baseline cannot be promoted to
stock-effect-verified. Lost-response tests cover selling all remaining stock and recovery
without a second write. A real installed-package USD15 mixed sample invoice matched both
saved lines, outstanding balance and stock decrease from two units to zero.
