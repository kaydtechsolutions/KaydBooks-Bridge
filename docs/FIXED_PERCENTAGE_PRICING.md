# Fixed-percentage customer pricing

For non-tax service invoices and sales receipts, an operator can bind one existing
customer price level in `invoice_masters.commercial`:

```json
{
  "sales_tax_code_id": "EXACT_NON_TAX_CODE_LIST_ID",
  "tax_item_id": null,
  "tax_rate": "0",
  "pricing": "fixed-percentage",
  "price_level": {"list_id": "EXACT_PRICE_LEVEL_LIST_ID", "percentage": "-25"},
  "inventory": "uncommitted-on-hand"
}
```

Use actual IDs from the intended bound company. A fresh compatibility read must
verify the customer's level, its active status, FixedPercentage type, percentage,
and enabled company preference. Changing the policy invalidates older evidence.
KB does not change customer records, price levels, or company preferences.

Each reviewed line must explicitly use the verified base service price multiplied
by `(1 + percentage / 100)`. For example, a USD1.00 service with a 25% discount
has a USD0.75 rate; quantity 70.4 produces USD52.80. The saved rate, quantity and
amount remain subject to independent readback.

This mode rejects per-item levels, foreign-currency levels, inventory items,
taxable sales, adjustments and rates requiring rounding beyond six decimals.
Configured percentages must be greater than -100 and at most 100. Default
`list-price` mode continues rejecting customers with price levels.

Reference: [Intuit QuickBooks SDK Programmer's Guide, Price Levels](https://static.developer.intuit.com/resources/QBSDK_ProGuide.pdf).
