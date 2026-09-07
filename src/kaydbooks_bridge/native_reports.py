"""Fixed native report requests and complete typed readbacks; no inferred totals."""

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, company_policy_context, strict_keys
from .validation import digest

REPORTS = {
    "profit-loss": ("GeneralSummary", "ProfitAndLossStandard", "period"),
    "balance-sheet": ("GeneralSummary", "BalanceSheetStandard", "as-of"),
    "trial-balance": ("GeneralSummary", "TrialBalance", "as-of"),
    "customer-balances": ("GeneralSummary", "CustomerBalanceSummary", "as-of"),
    "vendor-balances": ("GeneralSummary", "VendorBalanceSummary", "as-of"),
    "inventory-valuation": ("GeneralSummary", "InventoryValuationSummary", "as-of"),
    "inventory-stock": ("GeneralSummary", "InventoryStockStatusByItem", "as-of"),
    "sales-customers": ("GeneralSummary", "SalesByCustomerSummary", "period"),
    "sales-items": ("GeneralSummary", "SalesByItemSummary", "period"),
    "purchases-vendors": ("GeneralSummary", "PurchaseByVendorSummary", "period"),
    "purchases-items": ("GeneralSummary", "PurchaseByItemSummary", "period"),
    "unpaid-invoices": ("GeneralDetail", "OpenInvoices", "as-of"),
    "unpaid-bills": ("GeneralDetail", "UnpaidBillsDetail", "as-of"),
    "customer-statement": ("GeneralDetail", "CustomerBalanceDetail", "period"),
    "vendor-statement": ("GeneralDetail", "VendorBalanceDetail", "period"),
    "general-ledger": ("GeneralDetail", "GeneralLedger", "period"),
    "receivables-aging": ("Aging", "ARAgingSummary", "as-of"),
    "payables-aging": ("Aging", "APAgingSummary", "as-of"),
}
FIXED_ACCRUAL = {
    "customer-balances",
    "vendor-balances",
    "unpaid-invoices",
    "unpaid-bills",
    "inventory-valuation",
    "inventory-stock",
    "receivables-aging",
    "payables-aging",
}
FIXED_COLUMNS = {"inventory-valuation", "inventory-stock"}


def plan(policy, specification):
    strict_keys(
        specification,
        {"report", "date_to", "basis"},
        {"date_from", "entity_list_id", "item_list_id", "columns_by"},
    )
    name = specification["report"]
    if not isinstance(name, str) or name not in REPORTS:
        raise BridgeError("native report unavailable; tax reports are excluded")
    family, native, period = REPORTS[name]
    first, last = specification.get("date_from"), specification["date_to"]
    try:
        for value in [first, last] if period == "period" else [last]:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError()
            date.fromisoformat(value)
        if (period == "period" and first > last) or (period == "as-of" and first is not None):
            raise ValueError()
    except ValueError as exc:
        raise BridgeError("explicit valid period or as-of date required") from exc
    basis = specification["basis"]
    if basis not in ("Accrual", "Cash") or (name in FIXED_ACCRUAL and basis != "Accrual"):
        raise BridgeError("unsupported report basis")
    for field in ("entity_list_id", "item_list_id"):
        if field in specification and (
            not isinstance(specification[field], str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,31}", specification[field])
        ):
            raise BridgeError("exact report filter ListID required")
    columns_by = specification.get("columns_by", "TotalOnly")
    if columns_by not in ("TotalOnly", "Month", "Quarter", "Year") or (
        family != "GeneralSummary" and "columns_by" in specification
    ):
        raise BridgeError("unsupported report column grouping")
    if name in FIXED_COLUMNS and "columns_by" in specification:
        raise BridgeError("native inventory reports use fixed columns")
    if name in ("customer-statement", "vendor-statement") and "entity_list_id" not in specification:
        raise BridgeError("statements require an exact customer or vendor filter")
    return {
        "specification": specification,
        "family": family,
        "native_type": native,
        "date_mode": period,
        "currency": policy.currency,
        "columns_by": columns_by,
        "context_sha256": digest(
            {"policy": company_policy_context(policy), "report": specification}
        ),
    }


def append_queries(request, run, check):
    root = fromstring(request)
    batch = root[0]
    pref = ET.SubElement(batch, "PreferencesQueryRq", requestID=run + "3")
    ET.SubElement(pref, "IncludeRetElement").text = "MultiCurrencyPreferences"
    family, spec = check["family"], check["specification"]
    query = ET.SubElement(batch, family + "ReportQueryRq", requestID=run + "4")
    ET.SubElement(query, family + "ReportType").text = check["native_type"]
    ET.SubElement(query, "DisplayReport").text = "false"
    period = ET.SubElement(query, "ReportPeriod")
    if check["date_mode"] == "period":
        ET.SubElement(period, "FromReportDate").text = spec["date_from"]
    ET.SubElement(period, "ToReportDate").text = spec["date_to"]
    for field, node in (
        ("entity_list_id", "ReportEntityFilter"),
        ("item_list_id", "ReportItemFilter"),
    ):
        if field in spec:
            ET.SubElement(ET.SubElement(query, node), "ListID").text = spec[field]
    if spec["report"] in ("unpaid-invoices", "unpaid-bills"):
        ET.SubElement(ET.SubElement(query, "ReportTxnTypeFilter"), "TxnTypeFilter").text = (
            "Invoice" if spec["report"] == "unpaid-invoices" else "Bill"
        )
    if family == "GeneralSummary" and spec["report"] not in FIXED_COLUMNS:
        ET.SubElement(query, "SummarizeColumnsBy").text = check["columns_by"]
    if family == "GeneralDetail":
        ET.SubElement(query, "ReportOpenBalanceAsOf").text = "ReportEndDate"
    if family == "Aging":
        ET.SubElement(query, "ReportAgingAsOf").text = "ReportEndDate"
    elif spec["report"] not in FIXED_ACCRUAL:
        ET.SubElement(query, "ReportBasis").text = spec["basis"]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def one(node, name):
    matches = node.findall(name)
    if len(matches) != 1:
        raise BridgeError("native report field missing or duplicated")
    return matches[0]


def integer(value, maximum):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"0|[1-9][0-9]{0,6}", value)
        or int(value) > maximum
    ):
        raise BridgeError("native report count exceeds supported limits")
    return int(value)


def report_table(report):
    count = integer(one(report, "NumRows").text, 10000)
    width = integer(one(report, "NumColumns").text, 100)
    title_rows = integer(one(report, "NumColTitleRows").text, 10)
    columns = []
    for col in report.findall("ColDesc"):
        col_id = integer(col.get("colID"), width)
        if col_id < 1 or any(c["id"] == col_id for c in columns):
            raise BridgeError("duplicate or invalid report column")
        titles = []
        for title in col.findall("ColTitle"):
            index = integer(title.get("titleRow"), title_rows)
            if index < 1 or any(t["row"] == index for t in titles):
                raise BridgeError("invalid report column title")
            titles.append({"row": index, "value": title.get("value", "")})
        columns.append(
            {
                "id": col_id,
                "type": one(col, "ColType").text,
                "data_type": col.get("dataType"),
                "titles": titles,
            }
        )
    if {c["id"] for c in columns} != set(range(1, width + 1)):
        raise BridgeError("native report columns are incomplete")
    columns.sort(key=lambda c: c["id"])
    data = one(report, "ReportData")
    rows, seen = [], set()
    for row in data:
        if row.tag not in ("DataRow", "TextRow", "SubtotalRow", "TotalRow"):
            raise BridgeError("unsupported native report row")
        number = integer(row.get("rowNumber"), count)
        if number < 1 or number in seen:
            raise BridgeError("duplicate or invalid native report row")
        seen.add(number)
        label_nodes = row.findall("RowData")
        if len(label_nodes) > 1:
            raise BridgeError("ambiguous report row label")
        cells = {}
        for col in row.findall("ColData"):
            col_id = integer(col.get("colID"), width)
            if col_id < 1 or col_id in cells or "value" not in col.attrib:
                raise BridgeError("invalid or duplicate report cell")
            raw = col.attrib["value"]
            if len(raw) > 8192:
                raise BridgeError("oversized report cell")
            kind = col.get("dataType", columns[col_id - 1]["data_type"])
            # Preserve every native formatted value. Arithmetic never parses text cells as money.
            decimal_value = None
            if kind in ("AMTTYPE", "QUANTYPE", "PRICETYPE", "PERCENTTYPE") and raw:
                if not re.fullmatch(r"-?(?:[0-9]+)(?:\.[0-9]+)?", raw):
                    raise BridgeError("ambiguous native numeric report value")
                try:
                    decimal_value = format(Decimal(raw), "f")
                except InvalidOperation as exc:
                    raise BridgeError("invalid native numeric value") from exc
            cells[col_id] = {"value": raw, "data_type": kind, "decimal": decimal_value}
        rows.append(
            {
                "number": number,
                "kind": row.tag,
                "label": dict(label_nodes[0].attrib) if label_nodes else None,
                "text": row.get("value"),
                "cells": {str(k): v for k, v in cells.items()},
            }
        )
    if seen != set(range(1, count + 1)) or len(rows) != count:
        raise BridgeError("native report rows are incomplete; no partial report returned")
    rows.sort(key=lambda r: r["number"])
    return {
        "columns": columns,
        "rows": rows,
        "row_count": count,
        "column_count": width,
        "complete": True,
        "paging": "single native response; verified declared row and column counts",
        "native_totals": [row for row in rows if row["kind"] == "TotalRow"],
        "derived": False,
    }


def date_evidence(subtitle, check):
    """US native report headers echo the end date; some detail reports omit the start."""
    months = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    pattern = r"(" + "|".join(months) + r") ([1-9][0-9]?)(?:, ([0-9]{4}))?"
    matches = list(re.finditer(pattern, subtitle or ""))
    if not matches or not matches[-1].group(3):
        raise BridgeError("native report date header is unsupported or missing")
    try:
        last = date(
            int(matches[-1].group(3)),
            months.index(matches[-1].group(1)) + 1,
            int(matches[-1].group(2)),
        )
        first = None
        if len(matches) == 2:
            first = date(
                int(matches[0].group(3) or last.year),
                months.index(matches[0].group(1)) + 1,
                int(matches[0].group(2)),
            )
    except ValueError as exc:
        raise BridgeError("invalid native report date header") from exc
    spec = check["specification"]
    if last.isoformat() != spec["date_to"] or (
        first is not None and "date_from" in spec and first.isoformat() != spec["date_from"]
    ):
        raise BridgeError("native report date header differs from request")
    return {
        "native_end_date": last.isoformat(),
        "native_start_date": first.isoformat() if first else None,
        "start_date_evidence": "native-header"
        if first
        else "retained-request; native header omits start",
        "requested_start_date": spec.get("date_from"),
    }


def validate_response(response, run, check):
    if len(response.encode() if isinstance(response, str) else response) > 16 * 1024 * 1024:
        raise BridgeError("native report response exceeds size limit")
    root = fromstring(response)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 4:
        raise BridgeError("native report response count differs")
    pref, answer = root[0][2], root[0][3]
    for node, name, suffix in (
        (pref, "PreferencesQueryRs", "3"),
        (answer, check["family"] + "ReportQueryRs", "4"),
    ):
        if (
            node.tag != name
            or node.get("requestID") != run + suffix
            or node.get("statusCode") != "0"
            or node.get("statusSeverity") != "Info"
        ):
            raise BridgeError(
                "native report failed or returned a warning; unsupported result retained"
            )
    preference = one(pref, "PreferencesRet")
    if preference.findtext("MultiCurrencyPreferences/IsMultiCurrencyOn") != "false":
        raise BridgeError("native multi-currency report qualification is not yet available")
    report = one(answer, "ReportRet")
    basis = one(report, "ReportBasis").text
    if basis != check["specification"]["basis"]:
        raise BridgeError("native report basis differs from request")
    result = {
        **report_table(report),
        "report": check["specification"]["report"],
        "native_type": check["native_type"],
        "title": one(report, "ReportTitle").text,
        "subtitle": report.findtext("ReportSubtitle"),
        "basis": basis,
        "currency": check["currency"],
        "currency_basis": "verified-single-currency-company",
        "filters": check["specification"],
        "date_mode": check["date_mode"],
        "date_evidence": date_evidence(report.findtext("ReportSubtitle"), check),
        "context_sha256": check["context_sha256"],
        "response_sha256": hashlib.sha256(
            response.encode() if isinstance(response, str) else response
        ).hexdigest(),
    }
    root[0].remove(answer)
    root[0].remove(pref)
    return ET.tostring(root, encoding="unicode"), result
