# qbXML readback and recovery

## Compare request intent with response shape

QuickBooks does not always return the same element names used in an Add request. Comparison must translate documented request/return shapes without weakening the accounting checks.

For `AppliedToTxnAdd`, QuickBooks returns the current allocation as `AppliedToTxnRet/Amount`, not `PaymentAmount`. Compare those decimal values. Continue to compare `TxnID`, `DiscountAmount`, and `DiscountAccountRef` when supplied.

`AppliedToTxnRet/LinkedTxn` describes other transactions linked to the invoice. Do not treat linked amounts as allocations from the current payment.

`UnusedPayment` is the unapplied cash from the current receipt. Require zero when the reviewed allocation uses the full payment. `UnusedCredits` may describe other customer credits and can be nonzero even when the current payment was fully applied.

Normalize decimal strings before comparison. Preserve exact strings for names, descriptions, memos, references, and stable IDs. Require the same number and order of repeated transaction lines unless the operation has an explicitly documented order-insensitive response.

## Recovery states

- **Unsent:** no durable write handout exists. It is safe to correct the preflight/callback problem and create a new bounded run.
- **Acknowledged saved:** a correlated zero-status Add response contains one saved record with TxnID/ListID and EditSequence. If local verification code fails after receiving it, fix the verifier and resume at readback only. Do not issue another Add.
- **Handed out, response missing or transport uncertain:** pause and perform read-only duplicate/reference queries. If exactly one candidate matches the frozen request, verify by TxnID and attach it to the original attempt. If no conclusive match exists, keep it held.
- **Readback mismatch:** pause and retain the record and evidence. Do not overwrite, modify, delete, or resend unless the user separately authorizes a concrete correction after reviewing the saved state.

A recovery function must prove that the original write request and correlated successful response exist, no readback was already handed out, authorization and audit hashes remain valid, and authorization has not expired. It may change only the phase to readback and must record that no write was resent.

## Tests that protect the live path

Cover wrong company identity, revoked permissions, expired authorization, duplicate collisions, callback HCP omission after the first callback, interruptions, acknowledged-write recovery, altered saved fields, multiple payment legs sharing one reference, final pause, and immutable evidence. Tests should prove observable at-most-once and readback behavior, not merely generated wording.
