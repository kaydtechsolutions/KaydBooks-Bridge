# Ask Hermes for QuickBooks reports

Open the intended company in QuickBooks and keep its Web Connector connection
running under the same Windows user. Leave other company connections unselected
while qualifying a new company. Ask Hermes in normal language; JSON is unnecessary.

Always include the company name and the report date or date range. Hermes uses
`company_catalog_v1` to select the company's exact alias and connector, then
`qbwc_report_v1` to request fresh data. A queued request waits for Web Connector.
These tools read accounting data; they do not create transactions or send reports
to third parties.

## Main report menu

| Your menu number | Category | Main reports |
| --- | --- | --- |
| 11 | Company & Financial | Profit & Loss; Balance Sheet |
| 12 | Customers & Receivables | Customer Balance Summary; A/R Aging Summary; Open Invoices; Customer Balance Detail for a mapped customer |
| 13 | Sales | Sales by Customer Summary; Sales by Item Summary |
| 14 | Jobs, Time & Mileage | Job Profitability Summary; Time by Job Summary |
| 15 | Vendors & Payables | Vendor Balance Summary; A/P Aging Summary; Unpaid Bills Detail; Vendor Balance Detail for a mapped vendor |
| 16 | Purchases | Purchases by Vendor Summary; Purchases by Item Summary |
| 17 | Inventory | Inventory Valuation Summary; Inventory Stock Status by Item |
| 19 | Banking | Check Detail; Deposit Detail |
| 20 | Accountant & Taxes | Trial Balance; General Ledger; Journal |

This first set contains 23 report types. The two individual customer/vendor balance
detail reports require that company's verified entity mapping. They are balance
detail queries, not formatted statements for sending to customers.
Tax-specific, mileage, bank reconciliation, payroll and custom reports are outside
this first set. The menu category names do not imply that every submenu is enabled.

## Example messages

- “Get ISKAASHI ELECTRONICS Profit & Loss from September 1 to September 8, 2026,
  accrual basis. Use fresh QuickBooks data.”
- “Get ISKAASHI ELECTRONICS Balance Sheet as of September 8, 2026, accrual basis.”
- “Get ISKAASHI ELECTRONICS A/R Aging Summary as of September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Sales by Item from September 1 to September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Job Profitability from September 1 to September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Vendor Balance Summary as of September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Purchases by Vendor from September 1 to September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Inventory Valuation as of September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Check Detail from September 1 to September 8, 2026.”
- “Get ISKAASHI ELECTRONICS Trial Balance as of September 8, 2026.”

The examples are requests, not saved results or claims about balances.
Accrual is the default. Cash is available only for reports whose catalog permits it.
Job/time reports retain QuickBooks' native accounting-basis label, including None.

## Compare a result

Open the same report inside QuickBooks. Match the company, dates, cash/accrual
basis and filters. Compare the returned rows and native totals. Future-dated sample
transactions will not appear in a report with an earlier cutoff date.

An empty report is reported as empty; it is not evidence of a zero balance.
Incomplete, mismatched or unsupported responses are held. If a report is held,
share the error with the maintainer; do not ask Hermes to infer the missing figures.
Reports can return up to 10,000 rows within the response-size limit. Narrow the
requested period when a detail report exceeds those limits.

See [technical parameters and qualification](QBWC_REPORTS.md).
