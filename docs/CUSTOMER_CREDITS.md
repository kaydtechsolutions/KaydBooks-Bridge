# Customer service credit notes

`customer-credit.create` prepares an unapplied, non-tax service credit against one
original invoice. This path uses the company's private invoice customer/item/AR
mappings and single-currency commercial policy. Inventory returns, tax, currency
conversion, applying credits and refunds remain separate unqualified paths.

The payload is an invoice payload plus `invoice_txn_id`. The Bridge reads the exact
source invoice, active customer balance, current masters and complete customer
credit history. Original customer, AR account, date, line identities, quantities,
amounts and subtotal must agree. Unsupported units, grouped lines, taxes and
incomplete or ambiguous evidence are rejected.

Credit quantities and amounts cannot exceed the original invoice after prior
credits carrying the same `KaydBooks invoice <TxnID>` memo. This is a limit on
Bridge-linked credits; it does not identify every manually entered credit as a
return against that invoice. Preserve this source memo in QuickBooks.

`check_customer_credit_v1` produces read-only evidence;
`prepare_customer_credit_v1` links it to retained source bytes and an owned draft.
The SDK CLI offers `--credit-check` with a private payload JSON file. Preparation,
validation and submission do not dispatch accounting writes.

An operator can qualify the sample path using a private `sample_credit_posting`
gate with `connector`, `authorization`, `ref_prefix`, `max_credits` and `expires_at`.
The `post-sample-credit` CLI requires current scoped permission, approval policy,
fresh evidence, an unpaused company and no unresolved write. There is no production
dispatch path. Each attempt has a durable fence and is never resent.

A separate read-only session verifies the exact saved CreditMemo, source memo,
lines, total and fully unapplied `CreditRemaining`. Customer balance must decrease
by exactly the credit total against the retained pre-write baseline. A mismatch
holds the result for investigation. `reconcile-sample-credit` reads an uncertain
outcome without posting again. Concurrent accounting changes can prevent this
balance check from completing even when the credit exists.

Printing, email delivery, credit application and refunds are absent from this
request. Automated tests cover lost responses, balance mismatches, prior-credit
limits, source ambiguity and native request controls. Native sample qualification
is recorded separately in PROJECT_STATUS.md.
