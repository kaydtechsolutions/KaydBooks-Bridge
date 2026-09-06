# Apply an existing customer credit

`customer-credit.apply` links one existing credit memo to one invoice. It creates
no cash receipt, refund or money transfer. The local `ref_number` identifies the
Bridge job; it is not sent as a new QuickBooks transaction reference.

The payload requires `customer_id`, `invoice_txn_id`, `credit_txn_id`,
`total_amount`, `currency` and `ref_number`. The customer and receivable account
come from private company invoice mappings. This path supports non-tax,
single-currency invoice/credit pairs. Tax functionality is excluded from the release.

The read-only preflight verifies active customer/receivable masters, both exact
transactions, their customer and AR identities, balances and linked transactions.
Already-linked pairs and insufficient balances are rejected. Partial application
is supported; applying more to a previously linked pair is deliberately unavailable
until that SDK behavior is separately qualified.

The immutable request is a ReceivePaymentAdd with an AppliedToTxnAdd/SetCredit.
It contains no TotalAmount, PaymentAmount, payment method, bank account, printing,
email or discount. Intuit documents that this operation can return a lean response
without a new payment TxnID. The Bridge retains the invoice identity as the affected
record and labels the result `new_transaction_created: false`.

A separate read-only session must show the invoice balance and credit remaining
decreasing by the exact applied amount, customer balance unchanged, and matching
reciprocal invoice/credit links. The sample returns both link amounts as negative;
those signed observations are preserved and checked. The original baseline is retained in the audit.
Incomplete, conflicting or uncertain evidence remains held; recovery never resends
the application request. A returned new payment transaction is unexpected and held.

Use `check_credit_application_v1` and `prepare_credit_application_v1` for narrow
MCP read/prepare workflows, or SDK `--application-check` with a private JSON file.
Validation and queueing do not post. The separate CLI commands
`post-sample-application` and `reconcile-sample-application` require current scoped
permissions and a private `sample_application_posting` gate (`connector`,
`authorization`, `ref_prefix`, `max_applications`, `expires_at`). Company identity,
source review, approval and durable dispatch checks remain required. Production
posting is disabled.

Reference: [Intuit SDK Programmer's Guide, Chapter 16](https://static.developer.intuit.com/qbSDK-current/doc/pdf/QBSDK_ProGuide.pdf).
