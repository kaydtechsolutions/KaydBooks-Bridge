# Supplier credit application

`supplier-credit.apply` applies an existing vendor credit to an existing bill. It uses
exact private vendor, payable and bank mappings, current source review and the shared
durable job lifecycle. Production dispatch is disabled. The bank reference is required
by the SDK request; a credit-only application must leave its balance unchanged.

The payload contains `vendor_id`, `bank_id`, `bill_txn_id`, `credit_txn_id`,
`total_amount`, `currency` and `ref_number`. The reference identifies the local intent;
QuickBooks creates transaction links and may also create a zero-amount payment stub.
The Bridge retains the stub identity when QuickBooks returns one. It does not describe
that outcome as no new transaction.

A fresh read verifies the company, vendor, payable/bank accounts, both transactions,
reciprocal links and the complete vendor/AP `BillToPayQuery` result. The application
must not exceed either the bill balance or unused credit. An already linked pair is
rejected. Single-currency, non-tax transactions are supported; other variants fail
explicitly. `BillRet.OpenAmount` is never used as a bill-specific outstanding balance.

The native request contains one `BillPaymentCheckAdd` with `SetCredit` and an explicit
amount. It contains no `PaymentAmount`, printing instruction or bank-transfer operation.
A per-job immutable attempt and write-intent fence prevent automatic resends. A missing
response is held for read-only reconciliation. A returned payment TxnID must be a QuickBooks-generated zero-amount bill-payment stub
with exact vendor/AP/bank identities, no printing and no payment/discount amounts. It
requires a separate exact stub query. The query can omit allocation details; the Add
acknowledgement and independent bill/credit reciprocal links establish the application.
An unrelated or monetary payment is held for investigation.

Verification independently requires the bill and credit to decrease by the application,
reciprocal links to agree, and vendor and bank balances to stay unchanged. An exhausted
credit may disappear from the complete `CreditToApply` result, but exact transaction
reads and reciprocal links remain mandatory.

Private sample dispatch requires `sample_supplier_application_posting` with a confirmed
connector, authorization, reference prefix, expiry and bounded `max_applications`.
CLI commands are `post-sample-supplier-application` and
`reconcile-sample-supplier-application`. Read-only CLI discovery accepts
`--supplier-application-check`. Hermes exposes `check_supplier_application_v1` and
`prepare_supplier_application_v1`; neither dispatches accounting writes.

The request follows Intuit's *QuickBooks SDK Programmer's Guide*, chapter 16,
“Setting a Credit”: a credit-only bill payment links the existing bill and vendor
credit. The tested Enterprise 24 implementation additionally returned a zero-amount
stub; the validator follows the observed native evidence. Native qualification is recorded
separately in `PROJECT_STATUS.md` and the release checklist.
