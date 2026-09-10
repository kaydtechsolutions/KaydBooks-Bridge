# Native financial reports

`native_report_v1` and CLI `native-report REQUEST.json` read fixed reports through
the durable direct SDK transport. The request names an assigned company, its connector,
a unique numeric run ID and a specification containing report, dates, basis and optional
exact filters. The actor requires `report` and `read`; credentials stay in local configuration.
The same native helper requests read-only/no-personal-data access and cannot send a write.

The retained request and response are correlated with Host/Company evidence, confirmed
company identity and single-currency preferences. Complete native rows, typed columns,
row labels, subtitles, subtotals and totals are preserved. No detail/subtotal double counting
or inferred zero replaces missing data. Declared row/column counts, unique indices and
numeric types are checked. Warnings, missing rows, wrong company, mismatched basis or
native end-date headers stop the result. Limits are 10,000 rows, 100 columns and a 16 MB
response; incomplete results do not become reports. Reports use one complete native
response, not silent first-page truncation.

Every result records dates, basis, configured currency with verified single-currency status,
request context, raw-response SHA-256, SDK run reference, read-start time and report hash.
Some native detail headers echo only the end date: their start date remains explicitly
labelled as retained-request evidence. Cached reads retain their original timestamp and
are labelled snapshots, not fresh live balances. A new run ID requests a fresh read.
After an interrupted read, retained responses are reused; missing outcomes require explicit
read recovery. No accounting transaction is replayed. Permissions/configuration are
rechecked before returning the result.

| Report IDs | Native report family | Dates and supported basis |
| --- | --- | --- |
| `profit-loss` | ProfitAndLossStandard | Period; accrual or cash |
| `balance-sheet`, `trial-balance` | BalanceSheetStandard, TrialBalance | As-of; accrual or cash |
| `customer-balances`, `vendor-balances` | CustomerBalanceSummary, VendorBalanceSummary | As-of; fixed accrual |
| `unpaid-invoices`, `unpaid-bills` | OpenInvoices, UnpaidBillsDetail | As-of; fixed accrual; explicit Invoice/Bill filter |
| `customer-statement`, `vendor-statement` | CustomerBalanceDetail, VendorBalanceDetail | Period; exact entity required |
| `general-ledger` | GeneralLedger | Period |
| `receivables-aging`, `payables-aging` | ARAgingSummary, APAgingSummary | As-of; fixed accrual; native bucket titles retained |
| `inventory-valuation`, `inventory-stock` | InventoryValuationSummary, InventoryStockStatusByItem | Native as-of report; fixed basis and columns |
| `sales-customers`, `sales-items` | SalesByCustomerSummary, SalesByItemSummary | Period |
| `purchases-vendors`, `purchases-items` | PurchaseByVendorSummary, PurchaseByItemSummary | Period |

Period requests require `date_from` and `date_to`; as-of requests require only `date_to`.
Always specify `basis` as `Accrual` or supported `Cash`. Fixed-basis native reports reject
cash instead of silently substituting it. Optional `entity_list_id` and `item_list_id` are
exact native identities. Summary grouping supports `TotalOnly`, `Month`, `Quarter`, `Year`
where supported; inventory reports have fixed columns. The report schema is common,
but native parameter availability differs by report and unsupported requests fail explicitly.

The unpaid reports deliberately select invoices or bills. Their totals exclude unapplied
receipts/credits and therefore can differ from customer/vendor statements and aging.
Native sales and purchase reports also have their own accounting scopes; purchase-by-item
is not a substitute for all expense postings.

Sample evidence covers all 18 report IDs on US QuickBooks Enterprise 24 / qbXML 17,
single currency, accrual basis. Additional native checks cover cash P&L/balance sheet/trial
balance, an empty period, exact entity/item filters and monthly columns. Independent
comparisons matched P&L net income to balance-sheet income, assets to liabilities/equity,
trial-balance accounts to the general ledger, customer/vendor statements to balance and
aging summaries, and aging buckets to native totals. Accounting writes were zero.

Multi-currency reporting, inventory site/location variants and cash-flow reporting remain
unfinished. The reviewed GeneralSummary/GeneralDetail SDK enumeration has no direct
cash-flow report type; none is invented or represented by another report. The current
native matrix is supporting qualification and does not close every M6 acceptance gate.
Tax reports remain excluded by operator instruction. Browser report views remain M4-02 work.

Schema sources: Intuit's [GeneralSummaryReportQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/generalsummaryreportquery)
and [GeneralDetailReportQuery](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/generaldetailreportquery),
with their current qbXML 17 OSR definitions and retained native qualification responses.
