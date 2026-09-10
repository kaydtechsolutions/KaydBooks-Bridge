# Supplier bills through Web Connector

Browser bill checks now queue fixed QBWC supplier/account/item queries. Verified
evidence binds the exact payload, actor, company, connector and current policy;
stale checks and changed authority are rejected. The browser bill posting action
uses the shared durable QBWC lifecycle. Reconciliation of existing native attempts
retains its original transport.

Each separately authorized sample bill follows preflight, one BillAdd handoff and
independent exact-TxnID readback. The readback also queries BillToPay for the exact
vendor/payable account, verifies the outstanding liability, and compares stock
increase for inventory lines. Merely receiving a successful add response is not
verification. Reference duplicate checks remain vendor-scoped.

Disconnects, malformed results and lost responses hold the attempt. Recovery has
only search and readback stages; an absent result never authorizes a replacement
write. The original stock baseline remains mandatory. A never-started attempt can
close as not dispatched only with immutable proof of zero request handoffs. Failed
attempts continue consuming quota.

Invoice and bill contracts have separate limits, each shared across native and
QBWC attempts. Existing `qbwc_invoice_*` table names are retained for compatibility;
the immutable job operation selects the fixed contract. Read jobs gain an immutable
operation column, defaulting existing rows to invoices. Original evidence and
attempt histories are retained by the schema upgrade. There is no arbitrary XML
dispatch interface and no implicit native fallback for a new browser bill post.

Automated verification covers successful bill readback, lost responses, read-only
recovery, absent results, duplicate refusal, exact stock increase, stale/changed
master evidence, revoked permissions and immutable read operation. Installed
qualification verified one USD20 expense/purchased-service/inventory bill through
the operator's Web Connector schedule. All three lines and the USD20 BillToPay
balance matched; stock increased 0 -> 2 with average cost USD5. One attempt and one
write handoff were retained, repeat posting/recovery were refused, and response
hashes/audit passed. Posting was paused again. BillRet's OpenAmount reflected the
vendor aggregate, so the separate exact BillToPay result remains the bill balance
authority. Actual bill process-interruption qualification remains unfinished:
automatic approval review rejected the service-stop/fault-harness command before
execution. The prepared second candidate has zero write attempts, company posting
is paused, and the normal service remains running. Synthetic recovery tests are
not presented as actual interruption qualification.
Investigation confirmed the normal service remained running, the audit remained
valid and the candidate had zero attempts. The rejection provided no specific
policy rationale. The blocked command was not retried and approval controls were
not changed. Do not replay its preparation blindly: master evidence and the private
one-job grant must still be valid at dispatch time.

`tests/test_qbwc_bill_process_recovery.py` adds two isolated process-termination
checks. A child operating only on temporary synthetic company state exits without
`closeConnection`, either before or after accepting the simulated BillAdd response.
A new service waits for actual session expiration and recovers by reference search
and exact bill/payable readback. Both checks retain one attempt and one write,
assert zero recovery writes, refuse re-enqueue and verify the audit. Neither check
contacts QuickBooks or stops the installed Bridge, so actual QuickBooks interruption
qualification is still pending.

The affected suite passed 174 tests, with three further stock/permission cases
passing afterward. The default full suite passed 1,282 tests with 41 optional
browser/OCR tests skipped. A separate browser run passed 27 tests with one
OCR-dependent skip; the added bill waiting-state test and existing invoice
waiting-state test also passed. Browser wording now identifies queued bills and
labels both supported posting actions as Web Connector queueing. Production
posting remains disabled; the remaining entry types are tracked in
[data-entry readiness](DATA_ENTRY_READINESS.md).
