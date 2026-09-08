# Main reports through Web Connector

`qbwc_report_v1` exposes 23 fixed, read-only QuickBooks reports to Hermes.
It uses the shared QBWC read queue and requires the assigned company's `read` and
`report` permissions. It never uses the direct SDK, posts entries or sends messages.

The original five fields remain required and compatible:

```json
{
  "company": "company-a",
  "connector_id": "connector-company-a",
  "request_id": "balances-20260908-01",
  "report": "customer-balances",
  "date_to": "2026-09-08"
}
```

Use company and connector aliases from `company_catalog_v1`. Its `reports` and
`qbwc_reports.categories` describe the exact supported names and date modes.
As-of reports require `date_to` and omit `date_from`. Period reports require both.
`basis` defaults to `Accrual`; use `Cash` only when `fixed_accrual` is false.
Job/time reports do not accept a native basis selector and may return `None`;
that native value is preserved, never relabeled as cash or accrual.
Optional `entity_list_id` and `item_list_id` must be exact verified QuickBooks IDs.
Customer/vendor statements require `entity_list_id`; do not invent mappings.
Omit filters for all entities. `columns_by` accepts TotalOnly/Month/Quarter/Year
only on summary reports without fixed columns. Null optional fields are omitted.

| QuickBooks category | Main report names |
| --- | --- |
| 11 Company & Financial | `profit-loss`, `balance-sheet` |
| 12 Customers & Receivables | `customer-balances`, `receivables-aging`, `unpaid-invoices`, `customer-statement` |
| 13 Sales | `sales-customers`, `sales-items` |
| 14 Jobs, Time & Mileage | `job-profitability`, `time-by-job` |
| 15 Vendors & Payables | `vendor-balances`, `payables-aging`, `unpaid-bills`, `vendor-statement` |
| 16 Purchases | `purchases-vendors`, `purchases-items` |
| 17 Inventory | `inventory-valuation`, `inventory-stock` |
| 19 Banking | `check-detail`, `deposit-detail` |
| 20 Accountant & Taxes | `trial-balance`, `general-ledger`, `journal` |

This is the main-report set, not every submenu entry. Mileage, reconciliation,
tax-specific reports, payroll and custom reports are not part of this allowlist.
Statement aliases retrieve native balance detail; they do not produce or send
customer-facing statement documents.

For example, ask Hermes: "Get ISKAASHI's Profit & Loss from September 1 to
September 8, 2026, accrual basis. Use fresh QuickBooks data." The equivalent
report parameters add `"date_from": "2026-09-01"` to the original example and
change `report` to `profit-loss`, selecting the intended company's aliases.

Repeat identical parameters while `pending=true`, after Web Connector runs. Reuse
that request ID only for the same request. A new fresh snapshot needs a new ID.
If another read is queued, wait for it; do not clear the queue or request a write.

Success returns the company display name verified from the same QuickBooks response,
typed rows, unchanged native totals, requested/native date evidence, currency and
immutable read-start time. Responses older than five minutes, incomplete reports,
wrong-company responses and unsupported multicurrency results release no balances.
Closing a session again cannot renew the evidence timestamp. Unsupported report
types must not be routed to transaction checks or inferred from historical receipts.

Full calendar-month headings such as `August 2026` are expanded to the first and
last calendar dates, including leap years, and both boundaries must match the
requested period. Such a heading cannot validate an as-of request or a partial
month. Original native heading text remains in the returned report. A prior held
request stays held; use a new request ID for a fresh read after a validator update.

Live QuickBooks qualification showed Check Detail, Deposit Detail and Journal
reject `ReportBasis` with status 3151. Their requests now omit that unsupported
selector; the returned basis is still checked and preserved. They are advertised
with fixed accrual behavior on the qualified host. The legacy fixed SDK allowlist
is kept consistent with the QBWC builder; Hermes continues to use QBWC only.

Request names and XML field ordering follow Intuit's SDK Onscreen Reference:
[General Summary](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/GeneralSummaryReportQueryRq.json),
[General Detail](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/GeneralDetailReportQueryRq.json),
[Job](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/JobReportQueryRq.json),
[Time](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/TimeReportQueryRq.json),
[Aging](https://static.developer.intuit.com/qbSDK-current/common/newosr/qbsdk/json/AgingReportQueryRq.json).

Add `qbwc_report_v1` to the named Hermes profile's MCP tool allowlist and reconnect
that profile after deploying the Bridge update. `setup hermes` includes it in new
bundles. Previously generated bundles need this explicit allowlist addition. The
original seven data-entry tools and accounting/confirmation gates are unchanged.

This is an additional report route; the eight-entry v0.1.0 pilot score stays 15/15.

## Qualification

118 focused tests passed, including queue/restart/duplicate callbacks, native total
preservation, wrong-company and incomplete-response rejection, report permission
revocation, immutable selectors, stale evidence rejection and explicit MCP schema.
The installed Windows service completed an actual sample QBWC report on 2026-09-08.
The Linux Hermes host retrieved the same complete response through its SSH/MCP
launcher, with the company display name verified and native total retained. Posting
stayed paused, all accounting-attempt counts were unchanged, and database integrity
passed. Private evidence retains the actual company name, balances and source XML.

### Main-report expansion, 2026-09-08

161 focused tests passed across report framing, all 23 report selectors, explicit
MCP schemas, queue/restart/duplicate behavior, web catalog, direct-SDK allowlist
consistency and existing accounting gates. The installed Windows service and the
existing Linux Hermes SSH/MCP connection both exposed the expanded report catalog.
Hermes' actual MCP connection retrieved a complete native inventory report.

An authorized real company completed all 21 unfiltered main reports through its
own identity-bound QBWC connection. Period checks used September 1–8, 2026;
as-of checks used September 8, 2026. All returned complete typed rows with their
native basis and totals retained. Check Detail, Deposit Detail and Journal first
returned status 3151, then passed after the unsupported basis selector was removed.
This establishes actual native readback, not an operator's visual comparison of
every QuickBooks report screen. Private evidence retains each request, response,
result and the initial held checks.

The two filtered customer/vendor balance-detail selectors are implemented and
synthetic-tested; they require verified per-company entity mappings before that
company's individual statements can be live-qualified. They are not included in
the 21/21 unfiltered qualification score. Real-company permissions remain read/report
only with no posting gates. Accounting-job counts were unchanged, and database
integrity and audit verification passed for both configured companies.

The original v0.1.0 candidate ZIP remains unchanged. This development report update
is installed separately; it is not a published release or approval for production
data entry. See the [plain-language report guide](REPORTS_USER_GUIDE.md).
