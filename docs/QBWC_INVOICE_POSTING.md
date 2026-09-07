# Invoice posting and recovery through Web Connector

The HTTPS `/qbwc` endpoint now serves a durable, explicitly authorized sample-invoice
workflow alongside existing read-only discovery. It reuses the invoice contract,
company identity, source review, master evidence, approvals and saved-record
validators. Production posting remains disabled. Other transaction/report adapters
and scheduled posting have not been migrated by this change.

## Browser workflow

1. Enter an invoice and choose **Check details**. The Bridge queues a Web Connector
   master check without launching a direct-SDK helper.
2. Run **Update Selected** in Web Connector. Choose **Check details** again in the
   browser to retrieve the verified result, then save, validate and approve the draft.
3. Submit the approved invoice and choose **Queue in Web Connector**. This reserves
   one bounded sample attempt. It does not call QuickBooks from the browser process.
4. Run Web Connector again. One update performs the preflight, single write and
   independent invoice readback. Refresh the document to inspect its final state.

Queued checks are reused only for the same actor, connector, payload and current
master context within the evidence lifetime. Changes invalidate evidence. The fixed
write-stage preflight expires after two minutes; the queued posting lease is fifteen
minutes. A company pause or changed authority prevents a new write handoff.

The currently installed `.qwc` registration must permit the intended sample writes.
The existing `QWCProfile` generator remains a read-only qualification profile;
importing it does not grant posting access. Stable registration and the documented
AppLock permission requirements remain part of client setup qualification.

## Durable callback stages

Intuit's [Web Connector protocol](https://static.developer.intuit.com/qbSDK-current/doc/pdf/QBWC_proguide.pdf)
uses `sendRequestXML` and `receiveResponseXML` in successive exchanges. The Bridge
retains a separate immutable request/response record for each stage:

- **Preflight:** Verify Host/Company, current masters, price/accounts/settings and a
  reference-number duplicate query. An exact existing invoice advances to readback.
- **Write:** Recheck current actor/submitter/approver permissions, company binding,
  source/master evidence, pause, sample bounds and the exact approved request. Commit
  the write handoff before returning the `InvoiceAddRq`. Never hand it out twice.
- **Readback:** Verify the saved invoice independently by exact TxnID, including
  customer, accounts, lines, amounts, terms/adjustments and supported stock effects.
  The add response alone cannot mark the job verified.
- **Recovery:** An explicitly queued recovery searches for the original invoice,
  then independently reads it. It contains no write stage. An absent or inconsistent
  result stays unresolved; absence does not grant permission to try the write again.

Read requests can repeat safely across a service restart. An exact repeated response
returns its stored progress result. A repeated write request, conflicting callback,
bad XML, processor error, changed context, early close or expiration holds the job
for reconciliation. The shared company unresolved-write constraint prevents another
accounting write while an outcome is uncertain. Native and QBWC invoice attempts
consume the same cumulative sample quota.

If the first callback arrives after its evidence or dispatch lease expires, the
write is blocked. Recovery can close that held attempt as `qbwc_not_dispatched`
only when every run has no context, no saved transaction and no request handoff,
and no connector session is active. An immutable SQL-guarded resolution preserves
the attempt and consumed quota. It does not refresh evidence, requeue the job or
authorize resending. Once any request was handed out, normal reconciliation applies.

Inventory invoices require their original preflight stock baseline and exact saved
stock decrease, including recovery after the sale exhausted stock. Verified QBWC
receipts appear in browser review and the verified-original transaction selector.
Immutable SQL evidence and phase guards prohibit converting recovery into posting.

## Operator CLI

For an already reviewed, approved and submitted sample invoice:

```powershell
python -m kaydbooks_bridge.qbwc_posting enqueue `
  --config C:\BridgePrivate\bridge-config.json `
  --credentials C:\BridgePrivate\credentials.json `
  --principal operator --company company-a --job JOB_ID
```

Use `recover` instead of `enqueue` only for an owned uncertain QBWC invoice. Finish
or close the old Web Connector update before queuing recovery. Credentials stay in
private configuration. Neither command creates a posting grant or alters approvals.

## Qualification status

Automated SOAP tests cover successful posting/readback, exact repeats, restarts,
lost write responses, read-only recovery, missing results, wrong saved records,
inventory effects, stale/revoked authority, changed company/version, malformed
responses and immutable database evidence. Browser invoice checks and dispatch route
through QBWC. These tests use synthetic QuickBooks responses. Actual service-invoice
interruption/recovery is separately qualified below; other invoice variants still
require installed qualification.

Validation passed 1,300 full-suite tests with browser/offline OCR enabled, followed
by 27 focused posting/recovery tests and the browser waiting-state check for final
changes. Lint, formatting, JavaScript syntax and the package build passed.
Invoice-transport CI76 passed all 11 jobs.

### Installed sample invoice result

The actual Web Connector master check matched the confirmed sample company and
service invoice configuration. A separately approved USD5 non-tax service invoice
completed preflight (25%), one write handoff (75%) and independent exact-TxnID
readback (100%). The saved customer, date, line, subtotal, zero tax and USD5 remaining
balance matched. The operator also displayed the saved invoice and Audit Trail.

One durable attempt and one write handoff were retained. Re-enqueueing the verified
job and requesting recovery of that verified job were both refused. The company was
paused again, the cumulative invoice quota is exhausted and audit integrity passed.
Raw XML, receipt identifiers, company paths and authorization remain private.

Registration initially failed with a duplicate OwnerID/FileID message and a null
registry-name error. An explicit AppUniqueName alone did not resolve it. Intuit CP3
reported no FileID; a read-only query against the confirmed sample independently
found only an unlocked AppLock. After fully exiting/reopening Web Connector, the
same stable repair profile imported successfully and created its FileID. No new IDs
were generated. This records the observed sequence, not proof of one underlying
root cause. Windows Server required .NET 3.5 for CP3; installation was verified.

### Installed lost-write-response recovery

A second separately approved USD5 non-tax service invoice exercised an actual
process interruption. A private, single-job fault harness exited after receiving
QuickBooks' successful add response but before accepting it into the durable
lifecycle. Web Connector reported a connection failure. The saved write handoff
remained, with no accepted add response. The harness was then replaced by the normal
service; expiration using the actual clock held the job for reconciliation.

With posting paused, recovery performed only a reference search (75%) and independent
exact-TxnID readback (100%). The invoice identity, lines, amounts and USD5 remaining
balance matched, including the TxnID reported in the privately withheld response.
The job became verified with one original attempt, one total write handoff and no
recovery writes. Re-enqueue and recovery of the verified job were both refused.
Retained response hashes and audit integrity passed; no uncertain jobs or active
sessions remained. The bounded quota remains consumed. The six fault-selector
checks passed, and implementation CI78 passed all 11 jobs. Fault code and raw
company evidence are private and are not part of the distributed package.

Next: qualify an inventory invoice and its stock effects through actual QBWC.
Production posting and broader client release remain disabled.
