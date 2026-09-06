# Supplier credits and inventory returns

`supplier-credit.create` creates an unapplied vendor credit tied to an original
bill. The payload contains `vendor_id`, `bill_txn_id`, `txn_date`, `ref_number`,
`currency` and expense/service/inventory `lines`. Private bill mappings identify the vendor,
AP, expense accounts, purchased-service and inventory items. Due dates and terms do not apply.

The initial path supports single-currency, non-tax expense, service and mixed
credits and simple average-cost inventory returns. Inventory returns require enough
on-hand quantity at the verified average cost, validated asset/COGS/income accounts
and supported inventory settings. Broader currency/cost/inventory variants remain
unqualified. Credit application uses the separate `supplier-credit.apply` workflow.
Credit creation does not modify or delete the original bill.

Fresh evidence includes the exact original bill, active masters, complete vendor
credit history and AP-scoped BillToPay results. Credits sharing the Bridge source
bill memo count toward original amount and quantity limits. The memo records the
relationship; it is not a native credit application. External credits without that
memo cannot be attributed to this original bill by this limit check.

The saved credit must match vendor/AP, reference, date, original-bill memo and every
expense/service/inventory line. A separate `BillToPayQuery` must identify its unused amount
as `CreditToApply`. `VendorCreditRet.OpenAmount` is not used as unused-credit proof.
Vendor balance and net payable evidence must independently decrease by the credit.
Inventory returns additionally retain the original native stock baseline and require
an independent quantity decrease by the aggregated returned units. A sold-out return
can reconcile using that original baseline; it must not require stock for another
return. Missing or contradictory evidence remains held, and recovery never resends.

`check_supplier_credit_v1` reads evidence; `prepare_supplier_credit_v1` creates a
source-linked draft. Shared review, approval and submission apply. Native dispatch
uses `post-sample-supplier-credit`; read-only recovery uses
`reconcile-sample-supplier-credit`. A private `sample_supplier_credit_posting`
gate must specify `connector`, `authorization`, `ref_prefix`, `max_credits` and
`expires_at`. Production posting stays disabled.

Reference: [Intuit VendorCreditAdd](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/vendorcreditadd).

Native sample qualification: one USD10 two-unit inventory purchase followed by its
USD10 supplier credit verified stock 0 -> 2 -> 0 and vendor/net payable balances
23 -> 33 -> 23. Both writes passed independent readback on their first attempts.
