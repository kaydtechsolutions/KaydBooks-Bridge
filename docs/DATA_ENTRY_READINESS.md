# Required data-entry scope

The operator requested all fourteen entries below through QuickBooks Web Connector.
This checklist distinguishes implemented contracts from actual sample-company
qualification. Tax is excluded. Company bindings, credentials, mappings and test
evidence remain private. No production readiness is implied.

| Entry | Current Web Connector status |
| --- | --- |
| Invoice | Service and mixed simple-inventory invoices sample-qualified; service lost-response recovery qualified |
| Sales receipt | Mixed service/simple-inventory sample qualified: USD10, deposit +10, customer balance unchanged, stock -1; cash/check methods supported |
| Bill | Expense/purchased-service/inventory sample bill verified, including payable balance and stock increase; actual bill interruption qualification remains |
| Inventory transfer | Basic QBWC sample qualified: one unit source -1/destination +1; total stock and average cost unchanged |
| Credit memo | Mixed inventory/service USD15 credit sample-qualified through QBWC: customer balance USD59 → USD44, stock 2 → 4, unchanged average cost, one write; broader cases and actual interruption qualification remain |
| Bill credit | Mixed expense/service/inventory USD20 credit sample-qualified through QBWC: vendor/net payable balance USD54 → USD34, stock 4 → 2, one write; broader cases and actual interruption qualification remain |
| Account transfer | Not implemented |
| Customer payment | Basic partial payment sample-qualified through QBWC: USD5 payment, invoice outstanding USD15 → USD10, one write and valid audit; broader cases and actual interruption qualification remain |
| Supplier payment | Basic partial payment sample-qualified through QBWC: USD5 payment, selected bill outstanding USD20 → USD15, one write and valid audit; broader cases and actual interruption qualification remain |
| Check | Vendor expense check qualified: USD5, bank -5, expense +5, vendor payable unchanged |
| Journal | Balanced ordinary-account journal qualified: USD5 effect; original saved memos repaired with one memo-only Mod and exact readback |
| New item | Selected native item types exist; QBWC migration and remaining types required |
| New chart-of-accounts account | Not implemented |
| Batch Enter Transactions | CSV/XLSX intake exists; the requested Company-menu-style batch workflow remains unfinished |

Each new write contract needs typed input, current master/account checks, review and
approval, company-scoped authorization, immutable one-time handoff, independent saved
record and accounting-effect verification, and query-only uncertain-outcome recovery.
Batch readiness additionally needs supported row types, preview, per-row validation,
stable identities, partial completion and repeat-import behavior. Opening the native
QuickBooks batch window is not a Web Connector capability and is not substituted
for a verified Bridge workflow.

See [the primary connection decision](WEB_CONNECTOR_DIRECTION.md) and
[release acceptance gates](FIRST_RELEASE_SCOPE.md). Native evidence alone cannot
close Web Connector qualification. Remaining data-entry work must not be described
as ready merely because the shared transport passes tests.

Current selected-eight basic sample qualification is **8/8 (100%)**; the overall v0.1.0 workflow is **14/15 (93%)**. See [supported journal, check and transfer scope](QBWC_JOURNALS_CHECKS_TRANSFERS.md). Multi-location transfers do not qualify site-aware inventory sales/purchases.
