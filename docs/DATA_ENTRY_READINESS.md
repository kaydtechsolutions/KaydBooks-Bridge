# Required data-entry scope

The operator requested all fourteen entries below through QuickBooks Web Connector.
This checklist distinguishes implemented contracts from actual sample-company
qualification. Tax is excluded. Company bindings, credentials, mappings and test
evidence remain private. No production readiness is implied.

| Entry | Current Web Connector status |
| --- | --- |
| Invoice | Service and mixed simple-inventory invoices sample-qualified; service lost-response recovery qualified |
| Sales receipt | Not implemented |
| Bill | Expense/purchased-service/inventory sample bill verified, including payable balance and stock increase; actual bill interruption qualification remains |
| Inventory transfer | Not implemented |
| Credit memo | Native implementation exists; QBWC migration required |
| Bill credit | Native implementation exists; QBWC migration required |
| Account transfer | Not implemented |
| Customer payment | Native implementation exists; QBWC migration required |
| Supplier payment | Native implementation exists; QBWC migration required |
| Check | Standalone checks not implemented |
| Journal | Not implemented |
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
