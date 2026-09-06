# Invoice master compatibility

The direct SDK CLI supports `--invoice-check <private-payload.json>` as an alternative
to discovery, `--accounts`, `--list-id`, or `--master-preview`. It accepts the existing
invoice payload schema (customer alias, currency, date, reference and item/amount
lines). It does not prepare, submit or post a transaction or change invoice job state.

The principal needs company `read` and `validate` permission; explicit read recovery
also needs `recover`. Configure exact private master mappings alongside the existing
customer/item allowlists and `account_roles.invoice_receivable`:

```json
"invoice_masters": {
  "currency_id": null,
  "customers": {"customer-a": "synthetic-customer-id"},
  "items": {
    "item-a": {
      "list_id": "synthetic-service-id",
      "income_account_id": "synthetic-income-id"
    }
  }
}
```

Aliases must already be allowed by that company. Unknown fields, duplicate master IDs
under different aliases and malformed IDs are rejected. At most 20 distinct service
items are checked in one batch. Configuring an inventory item as a service item does
not make it supported: the exact service query must actually return that record.

## Currency evidence

`currency_id: null` explicitly selects single-currency policy. The invoice must match
the operator-configured company currency, and QuickBooks preferences must report
multicurrency disabled. Unexpected currency references block the check. The result
reports `currency_basis=configured-single-currency`: the base-currency code comes from
private operator policy, not independent SDK verification. Do not infer it from names
or locale. The operator is responsible for the correctness of that configured code.

With an explicit currency ListID, Preferences HomeCurrencyRef, the exact CurrencyRet
code, customer CurrencyRef and receivables CurrencyRef must all agree with the invoice
and configured base currency. The result reports `verified-home-currency`. Foreign
currency invoices, missing references, warnings and unsupported currency queries block
this mode. No exchange-rate calculation or multicurrency setting changes are made.

## Customer and item evidence

The fixed batch reads Host, Company, Preferences, optional Currency, the receivables
Account, Customer, and each ItemService plus its configured income Account. Every exact
query must return one active matching ListID with success status and correct request
correlation. The receivables account must be AccountsReceivable. A service item must
have exactly one SalesOrPurchase.AccountRef or SalesAndPurchase.IncomeAccountRef matching
the configured active Income account. Missing or ambiguous sales data is rejected.

Company binding is reverified before returning any successful result. Results contain
hashes/counts, not customer data or raw XML. Native C# enforces the fixed query names,
exact selectors, bounded count and projected fields. Private payload and policy hashes
are immutable for the durable run; a changed invoice or mapping requires a new run ID.
Saved-response replay revalidates permissions, policy and evidence without redispatch.

The result is `scope=master-evidence-only`. It does not establish tax correctness,
credit availability, pricing, discounts, parent-job eligibility, inventory/site/lot
availability or all transaction restrictions. It is not a posting approval or a promise
that a later write would use unchanged master data. A future posting adapter must
revalidate relevant facts before dispatch. QBWC also supports these bounded invoice
checks through an authenticated one-update queue, described below.

## Bounded master preview

`--master-preview` reads up to 20 active currencies, customers, service items and accounts
plus preferences, after authentication and company binding. It returns private records
and always `complete=false`. It does not automatically choose production mappings.
For discovery only, CurrencyQuery error 3250 with no records is accepted when the same
batch explicitly reports multicurrency off; the currency list stays empty. Other errors
remain blocked. This exception never establishes a verified currency code.

## Qualification

The sample company returned a verified preview and passed a new exact service-item
invoice master check with `configured-single-currency` basis. The first preview was
held when QuickBooks returned CurrencyQuery 3250; the explicit disabled-multicurrency
discovery handling above was then added and a new preview succeeded. No old blocked
evidence was rewritten. New exact account/customer/item evidence and a fresh-process
replay passed with valid audit and no redispatch. No invoice was posted.

The explicit CurrencyRef mode is synthetically tested; it is not real-qualified on
this sample because multicurrency is off. Tests cover wrong/missing/inactive masters,
currency mismatch, permission and policy changes, payload immutability, replay,
correlation, cardinality, warnings and the native field allowlist.

Schema references: Intuit's Desktop [PreferencesQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/preferencesquery),
[CustomerQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/customerquery),
[ItemServiceQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/itemservicequery),
and [CurrencyQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/currencyquery).
The corresponding official qbSDK-current JSON request/response schemas were inspected.


## QBWC invoice compatibility

Use `python -m kaydbooks_bridge.qbwc_invoices` with `--config`, `--credentials`,
`--principal`, `--connector`, `--job`, `--payload <private-payload.json>` and `--enqueue`.
Retrieve the result using the same arguments without payload/enqueue. This is a read
job, not an invoice posting request. It requires the same read+validate permissions,
exact mappings, confirmed company and shared compatibility rules as direct SDK.

Each job binds its owner, connector, canonical payload and policy hash immutably to
one future QBWC session. Only one unassigned account or invoice read job may wait
per connector. Existing active sessions are not changed. Repeating the same enqueue
is idempotent; changing the payload/policy needs a new job. Consumed jobs are never
silently moved to another session after disconnect or expiry. A failed queued policy
check is held, and new result reads recheck current configured policy and permissions.
Configuration changes require reloading/restarting the service before callbacks use them.

The callback requires matching HCP evidence and US qbXML 17.0 before returning the
fixed request batch. Restart reuses the persisted exact request. Response validation
reuses the direct SDK master checks and company verifier. Later ordinary connector
updates return to discovery. Posting and Auto-Run remain disabled in qualification.

Real sample QBWC qualification passed with the same service-item payload and policy
as direct SDK: closed session, response_result 100, compatibility matched, no error,
verified binding and valid audit. Fresh-process CLI retrieval also passed. Currency
basis is configured-single-currency; explicit CurrencyRef mode remains synthetic-only.
Synthetic coverage includes repeated callbacks, restart, both currency modes, immutable
context, changed permission/policy, queue exclusion, disconnect, missing HCP, unsupported
versions and incompatible/wrong-company responses. No production invoice was posted.
