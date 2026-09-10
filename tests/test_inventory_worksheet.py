from copy import deepcopy

import pytest

from kaydbooks_bridge.inventory_worksheet import paginate, site_items


def report_fixture():
    def item(name, quantity):
        return {
            "kind": "DataRow",
            "label": {"rowType": "item", "value": name},
            "cells": {"1": {"value": name}, "2": {"decimal": quantity}},
        }

    return {
        "company_identity_verified": True,
        "company_display_name": "Test Company",
        "report": {
            "complete": True,
            "native_type": "InventoryValuationSummaryBySite",
            "date_evidence": {"native_end_date": "2026-09-09"},
            "columns": [{"id": 2, "type": "QuantityOnHand"}],
            "rows": [
                {"kind": "TextRow", "text": "Site A"},
                item("Positive", "5"),
                item("Negative", "-2"),
                item("Zero", "0"),
                {
                    "kind": "SubtotalRow",
                    "label": {"rowType": "inventorySite", "value": "Site A"},
                    "cells": {"2": {"decimal": "3"}},
                },
                {"kind": "TextRow", "text": "Site B"},
                item("Other site", "999"),
            ],
        },
    }


def select(result):
    return site_items(result, company="Test Company", site="Site A", report_date="2026-09-09")


def test_exact_site_selection_retains_negatives_and_excludes_only_zero():
    items, counts = select(report_fixture())
    assert [(item["name"], item["quantity"]) for item in items] == [
        ("Positive", "5"),
        ("Negative", "-2"),
    ]
    assert counts["excluded_zero"] == 1
    assert counts["native_quantity_total"] == "3"


@pytest.mark.parametrize(
    "problem", ["company", "date", "total", "duplicate", "missing_quantity", "missing_end"]
)
def test_incomplete_or_ambiguous_evidence_is_rejected(problem):
    result = deepcopy(report_fixture())
    report = result["report"]
    if problem == "company":
        result["company_display_name"] = "Other Company"
    elif problem == "date":
        report["date_evidence"]["native_end_date"] = "2026-09-08"
    elif problem == "total":
        report["rows"][4]["cells"]["2"]["decimal"] = "4"
    elif problem == "duplicate":
        report["rows"].insert(2, deepcopy(report["rows"][1]))
    elif problem == "missing_quantity":
        report["rows"][1]["cells"].pop("2")
    else:
        report["rows"].pop(4)
    with pytest.raises(ValueError):
        select(result)


@pytest.mark.parametrize("anchor_index", [0, 2, 4, 7, 8])
def test_anchor_always_starts_page_without_empty_pages(anchor_index):
    items = [{"name": str(i)} for i in range(12)]
    items[anchor_index]["name"] = "DAARADAMIYE 1 GAN"
    pages = paginate(items, capacity=4)
    assert all(pages)
    assert [item for page in pages for item in page] == items
    assert next(page for page in pages if items[anchor_index] in page)[0] == items[anchor_index]
    assert all(len(page) <= 4 for page in pages)
