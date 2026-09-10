# Inventory counting worksheets

The read-only QBWC report `inventory-by-site` requests QuickBooks'
`InventoryValuationSummaryBySite` for an explicit as-of date. Its native
`QuantityOnHand` column can populate a printable stock-count worksheet. This
selector has fixed inventory columns and accrual behavior; it does not use the
company-wide stock report as a substitute for an individual site's stock.

`inventory_worksheet.site_items` requires a verified company, complete native
report, matching native end date, one exact site section, unique item identities,
and an exact match between the item quantities and the native site subtotal.
Only zero-stock rows are excluded. Negative stock is retained. Names, quantities,
ordering and totals come from the fresh source, not an older PDF template.

`inventory_worksheet.render` generates a letter-size counting sheet with repeated
headers and blank count/comment columns. Its page-break item begins a fresh page
independently of its item index or resulting page number. `reportlab` is required
only for rendering; it is not imported by the Bridge service.

Before delivery, extract the PDF table back into rows and compare every identity,
quantity and blank column against the source. Render every page for visual review.
Where the operator requires comparison with the separate QuickBooks **Quantity on
Hand by Site** report, retain that independent report and its comparison evidence
as an additional delivery gate. The valuation report alone does not establish
that this separate comparison was performed.

Company settings, source results, generated PDFs and destination-specific delivery
receipts belong in private directories outside Git. A recurring report should run
only after the complete business-date batch is verified, with explicit date,
weekend, revision and duplicate-delivery rules.
