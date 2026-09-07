# Customer receipts and payment allocation

`customer-payment.create` prepares a recorded accounting receipt, with exact customer,
AR, deposit-account and payment-method mappings. It does not initiate a bank transfer
or card charge. Production posting is disabled.

Private `payment_masters` contains `customers`, `receivable`, `deposits` and `methods`.
The customer, deposit and method values are alias-to-ListID maps. Optional
`allow_unapplied` enables an explicit unallocated remainder; it defaults to false.
No deployment identity, path, credential or account mapping belongs in this repository.

Payload fields are `customer_id`, `deposit_id`, `method_id`, `txn_date`, `ref_number`,
`currency`, `total_amount` and `allocations` (unique `txn_id`/`amount` pairs). Amounts
use decimal strings. Allocation sums cannot exceed the receipt amount or the freshly
queried invoice balance. Customer/AR identities and dates must match. Supported native
tests currently cover single-currency Cash/Check accounting methods and Bank or the
specific UndepositedFunds account type. Explicit [settlement discounts](SETTLEMENT_DISCOUNTS.md)
are supported per allocation. Multicurrency, credit application, refunds and processing
card charges are not implemented by this path.

## Workflow and evidence

The fixed SDK `--payment-methods` preview is bounded; `--payment-check PRIVATE_JSON`
provides exact master and invoice-balance evidence. Captured sources, field confidence,
review, prepare/validate/preview/submit and current grants use the shared durable
lifecycle. MCP `check_customer_payment_v1` and `prepare_customer_payment_v1` expose
lookup and preparation only. They do not expose native posting.

CLI `post-sample-payment JOB` requires a private `sample_payment_posting` gate with
connector, authorization, ref_prefix, max_payments and expires_at. Each attempt has
an immutable request, exact reference duplicate check, original invoice balance
baseline, a final authority check and a single write fence. `reconcile-sample-payment
JOB` reads an uncertain outcome; it never resends the accounting request. Unknown
payments cannot enter invoice/bill dispatch or simulation recovery.

An independent native session verifies the saved receipt, exact allocation amounts,
unused payment and invoice balance effects. Fully unapplied receipts send
`IsAutoApply=false`; they must return no invoice allocations. Historical verified
receipts remain observations at their recorded time, not claims about current balances.

Three QuickBooks behaviors are explicitly handled:

- InvoiceRet.AppliedAmount is signed: a USD15 settled invoice returns -15 applied and
  zero remaining. Validation normalizes the sign and retains the native observation.
- AppliedToTxnRet.LinkedTxn can list other payments associated with the same invoice.
  These are retained as related history, never counted as this payment's allocation.
- ReceivePaymentRet.UnusedCredits can include other available customer credits.
  It is a nonnegative diagnostic observation, not this receipt's UnusedPayment.

## Native sample qualification

A USD5 partial receipt and USD10 final receipt settled the USD15 mixed invoice. The
initial sign/history validation errors were fixed and both outcomes reconciled without
resending. A third USD5 unapplied receipt was saved with no automatic allocation.
Fresh exact reads of all three receipts pass after another credit exists.

An independent private SDK diagnostic queried Customer Balance Summary both as of the
transaction date and for all transactions. Both reports and CustomerRet.Balance agreed
on USD25: USD30 across the three older unpaid invoices minus USD5 unapplied. This
diagnostic sent no accounting writes. The product's historical invoice register is
still not a current receivables report; native financial-report APIs remain release work.

Automated tests cover partial/full settlement, explicit unapplied receipts, revoked
authority, wrong balances, other available credits, prior linked payments, lost replies,
read-only recovery and refusal to dispatch a second time. Broader payment variants
and native interruption scenarios remain unchecked in the first-release checklist.
