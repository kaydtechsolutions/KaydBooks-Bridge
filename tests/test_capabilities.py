from kaydbooks_bridge.capabilities import inventory


def test_v01_capabilities_report_all_selected_entries_without_enabling_production():
    result = inventory()
    transactions = result["bridge"]["selected_transactions"]
    assert set(transactions) == {
        "sales-receipt.create",
        "invoice.create",
        "customer-credit.create",
        "customer-payment.create",
        "bill.create",
        "journal.create",
        "inventory-transfer.create",
        "check.create",
    }
    assert set(transactions.values()) == {"controlled_sample_qualified"}
    assert result["bridge"]["tax"] == "excluded_from_v0.1.0"
    assert result["bridge"]["production_posting"] == "disabled"
    assert not result["live_posting"]
