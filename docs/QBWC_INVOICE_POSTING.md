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
through QBWC. These tests use synthetic QuickBooks responses; installed sample
posting/recovery qualification remains pending until actual Web Connector updates
have completed and their saved accounting results are verified.

Validation passed 1,300 full-suite tests with browser/offline OCR enabled, followed
by 27 focused posting/recovery tests and the browser waiting-state check for final
changes. Lint, formatting, JavaScript syntax and the package build passed. A private
read-only sample invoice check is queued; no new accounting write was sent.
