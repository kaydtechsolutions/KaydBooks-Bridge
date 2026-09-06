# Supplier payments

`supplier-payment.create` records an explicitly allocated bill payment using
BillPaymentCheckAdd. It does not transfer funds, print a check or contact a bank.
Production posting remains disabled.

Private `supplier_payment_masters` contains `vendors` and `banks` alias-to-ListID
maps and an exact `payable` account ListID. The payload requires `vendor_id`,
`bank_id`, `txn_date`, `ref_number`, `currency`, `total_amount` and one to twenty
unique `allocations` (`txn_id`/`amount`). Decimal allocation amounts must sum to the
payment total. Credit applications, discounts, unapplied payments, credit cards,
multicurrency and printing are unavailable in this path.

Fixed read-only checks verify active vendor, AP and Bank account identities, exact
bill dates and vendor/AP bindings, single currency and a complete vendor/AP-scoped
BillToPay response. Per-bill outstanding amounts come from BillToPay, while
BillRet.OpenAmount remains diagnostic. A paid bill can have zero outstanding only
when its exact BillRet reports IsPaid and the complete payable query agrees.

The shared source, review, permission, preparation and queue lifecycle applies.
`--supplier-payment-check PRIVATE_JSON` performs the native read check. MCP
`check_supplier_payment_v1` and `prepare_supplier_payment_v1` expose read/preparation,
not posting. CLI `post-sample-supplier-payment JOB` requires a private
`sample_supplier_payment_posting` gate: connector, authorization, ref_prefix,
max_payments and expires_at.

Each native attempt retains its request, company binding, authority check and bill
balance baseline. A single-write fence prevents automatic resend. An independent
native session compares the saved payment, exact allocations and expected balance
decrease. `reconcile-sample-supplier-payment JOB` recovers an uncertain outcome using
reads only. Prior linked payments are related history, not additional allocations.

Two installed-package USD5 sample payments settled an isolated USD10 bill. Separate
VendorQuery, complete BillToPay and Vendor Balance Summary all agreed on USD30 across
the three remaining unpaid bills. No payment was resent. Tests cover partial/full
settlement, missing replies, paid-bill recovery, duplicates, ambiguous/partial payable
responses, authority changes, wrong account/vendor and unsupported applications.

This is the single-currency bank-account sample path; the broader M3-05 gate remains
partial. Real identities, references, account mappings and proof files stay private.
