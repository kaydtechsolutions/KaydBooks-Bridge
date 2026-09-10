# Reviewed batch workflow

Use this runbook for an explicitly authorized live or qualification batch.

## 1. Preserve and qualify the source

Copy the reviewed source into a private evidence directory and hash the original bytes. Parse monetary values with decimal arithmetic. Validate line extensions, transaction totals, payment allocations, journal debit/credit equality, currency, tax treatment, and the expected financial-entry count.

Order by the numeric prefix on the source documents, not by transaction type, filename lexical quirks, JSON array accidents, or QuickBooks reference number. If one source produces multiple entries, keep those entries adjacent and in the confirmed leg order.

Freeze one immutable authorization envelope containing the source hash, target company and connector identity, expiry, entries, private ListID mappings, accepted-existing exceptions, and approved corrections. Preview every exact qbXML write before activation.

## 2. Resolve exact QuickBooks state

Use read-only QBWC queries to collect Host and Company identity; customers, vendors, items, accounts, terms, sales reps, payment methods, inventory sites, names, and the non-tax code; exact-reference candidates; invoice balances; and preferences such as multicurrency.

### Start the ISKAASHIRDP Web Connector session cleanly

For ISKAASHI runs that use the existing ISKAASHIRDP QuickBooks/Web Connector session, verify the session state before queuing or retrying a query:

- Confirm the exact Web Connector application has QuickBooks permission to read and modify the company file, with **Allow this application to login automatically** saved for the intended QuickBooks user. A permission dialog is not saved until the operator selects **OK** and accepts any follow-up confirmation prompt.
- Once automatic login is configured, close the interactive QuickBooks instance before triggering Web Connector. Let Web Connector open the configured company file under the saved QuickBooks user.
- Before retrying after a connection failure, inspect QuickBooks processes in the ISKAASHIRDP session. Do not trigger while both an interactive process and a stale `QBW.EXE -Embedding` process remain. A stale embedding process from a conclusively failed, closed QBWC session may be stopped after its session, command line, and failed callback state are verified; never terminate an interactive QuickBooks process on the operator's behalf.
- Treat `0x80040438` as a multiple-instance/session-state failure. Clear the stale failed-session process or have the operator close interactive QuickBooks, then create a fresh read-only request ID.
- Treat `0x8004041D` as missing or unsaved automatic-login permission for the exact connector identity. Have the QuickBooks administrator save that permission, close QuickBooks, and retry with a fresh read-only request ID.
- Keep posting paused throughout connection repair. Failed read-only requests are conclusive unsent attempts and must remain in the evidence history.

Reject inactive, missing, ambiguous, or changed masters. Match by stable ListID after the reviewed FullName, Name, or Initial is confirmed. Do not store this catalog in Git.

For a newly authorized customer, create it as a separate dependency step, read back its ListID, then use that customer for the dependent transaction. The dependency does not increase the financial-entry denominator.

## 3. Dispatch each entry

For each entry, execute three durable phases:

1. **Preflight:** recheck company identity, duplicate reference, preferences, allocation balances, and every referenced master.
2. **Write:** record the immutable request and handout time before returning it to Web Connector. Accept one response only.
3. **Readback:** query the returned TxnID/ListID independently, verify saved fields and accounting effects, then advance.

The Web Connector may provide the HCP company payload only on the first `sendRequestXML` callback. Persist the verified HCP in the durable session and require later callbacks to reuse that saved identity. Continue to validate the callback company-file path and qbXML version on every callback.

Do not mix an active reviewed batch with native SDK writes, another QBWC write workflow, or another reviewed batch for the same company.

## 4. Verify accounting effects

For invoices and sales receipts, verify customer, date, reference, sales rep, terms when supplied, all item lines, descriptions, quantities, rates, amounts, inventory site, non-tax code, total, and zero tax. If the source intentionally has no terms, confirm QuickBooks did not inherit terms unexpectedly.

For payments, verify customer, accounts, method, date, reference, total, each invoice application, discount and discount account, zero unused payment, and before/after invoice balances. Existing customer credits reported by QuickBooks are contextual evidence; do not confuse them with unused cash from the current payment.

For checks, verify bank account, payee, reference, date, memo, expense accounts, line amounts, total, and print state. Preserve source-only metadata that has no native QuickBooks field in the private authorization/audit evidence.

For journals, verify adjustment flag, date, reference, each debit and credit account, exact amount, memo, entity name, and balanced totals.

## 5. Close the run

Pause posting after the final verified readback. Recheck the audit chain, expected result count, source hash, duplicate exceptions, payment balances, and requested reconciliation totals. Export approved input, exact requests, exact responses, normalized saved records, and final status to the private evidence directory.

If an earlier attempt failed before any write handout, retain it as a conclusively unsent attempt. A replacement run may use the same immutable authorization data with a new run ID; preserve both histories.
