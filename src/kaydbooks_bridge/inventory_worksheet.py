"""Printable counting worksheet from a verified native inventory-by-site report."""

from datetime import date
from decimal import Decimal, InvalidOperation


def site_items(result, *, company, site, report_date):
    """Select one complete site section; verify its exact native quantity total."""
    report = result.get("report", {})
    if not result.get("company_identity_verified") or result.get("company_display_name") != company:
        raise ValueError("Verified report company differs")
    if (
        not report.get("complete")
        or report.get("native_type") != "InventoryValuationSummaryBySite"
        or report.get("date_evidence", {}).get("native_end_date") != report_date
    ):
        raise ValueError("Complete native inventory-by-site report for the exact date required")
    columns = [str(c["id"]) for c in report["columns"] if c["type"] == "QuantityOnHand"]
    if len(columns) != 1:
        raise ValueError("Exact quantity-on-hand column required")
    quantity_column = columns[0]
    starts = [
        i
        for i, row in enumerate(report["rows"])
        if row["kind"] == "TextRow" and row["text"] == site
    ]
    ends = [
        i
        for i, row in enumerate(report["rows"])
        if row["kind"] == "SubtotalRow"
        and row.get("label") == {"rowType": "inventorySite", "value": site}
    ]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Unique complete site section required")
    section = report["rows"][starts[0] + 1 : ends[0]]
    items, seen = [], set()
    for row in section:
        if row["kind"] != "DataRow":
            continue
        label = row.get("label") or {}
        full_name = label.get("value")
        if label.get("rowType") != "item" or not full_name or full_name in seen:
            raise ValueError("Missing or duplicate item identity")
        seen.add(full_name)
        try:
            quantity = Decimal(row["cells"][quantity_column]["decimal"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("Missing exact native quantity") from exc
        if not quantity.is_finite():
            raise ValueError("Non-finite native quantity")
        name = row["cells"].get("1", {}).get("value")
        leaf = full_name.split(":")[-1]
        # QuickBooks labels a parent item's own stock as "<name> - Other".
        if not name or name not in (leaf, leaf + " - Other"):
            raise ValueError("Item display name differs from native identity")
        items.append({"full_name": full_name, "name": name, "quantity": format(quantity, "f")})
    total = Decimal(report["rows"][ends[0]]["cells"][quantity_column]["decimal"])
    if not items or sum(Decimal(item["quantity"]) for item in items) != total:
        raise ValueError("Item quantities differ from native site total")
    included = [item for item in items if Decimal(item["quantity"]) != 0]
    # The counting worksheet displays leaf names, so ambiguous leaf names cannot be hidden.
    if len({" ".join(item["name"].split()) for item in included}) != len(included):
        raise ValueError("Ambiguous displayed item names")
    return included, {
        "all_items": len(items),
        "excluded_zero": len(items) - len(included),
        "included_items": len(included),
        "native_quantity_total": str(total),
    }


def paginate(items, *, anchor="DAARADAMIYE 1GAN", capacity=52):
    if capacity < 1:
        raise ValueError("Positive page capacity required")

    def normalize(value):
        return "".join(value.upper().split())

    matches = [i for i, item in enumerate(items) if normalize(item["name"]) == normalize(anchor)]
    if len(matches) != 1:
        raise ValueError("Exactly one required page-break item must be present")
    pages, page = [], []
    for index, item in enumerate(items):
        if page and (len(page) == capacity or index == matches[0]):
            pages.append(page)
            page = []
        page.append(item)
    if page:
        pages.append(page)
    return pages


def render(path, items, *, company, site, report_date, generated_time, font_path):
    """Letter paper, compact continuous grid, with a repeatable forced page break."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("WorksheetArialBold", str(font_path)))
    font = "WorksheetArialBold"
    pages = paginate(items)
    c = canvas.Canvas(str(path), pagesize=(612, 792), pageCompression=1)
    c.setTitle(f"{company} - Quantity on Hand by Site - {site} - {report_date}")
    c.setAuthor(company)
    display_date = date.fromisoformat(report_date)
    # Positions reproduce the supplied worksheet; a slightly wider QB column fits four digits.
    xs = [15, 37, 241, 271, 304, 339, 369, 597]
    top, header_height, row_height = 734, 16, 13.08
    sequence = 0
    for page_number, page in enumerate(pages, 1):
        c.setFont(font, 8)
        c.drawString(16, 777, generated_time)
        c.drawString(16, 767, display_date.strftime("%m-%d-%y"))
        c.setFont(font, 12)
        c.drawCentredString(306, 774, company)
        c.drawCentredString(306, 757, f"Quantity on Hand by Site - {site}")
        c.setFont(font, 10)
        c.drawCentredString(306, 744, f"{display_date:%B} {display_date.day}, {display_date.year}")
        bottom = top - header_height - row_height * len(page)
        c.setLineWidth(0.8)
        for x in xs:
            c.line(x, bottom, x, top)
        for y in [top] + [top - header_height - row_height * n for n in range(len(page) + 1)]:
            c.line(xs[0], y, xs[-1], y)
        c.setFont(font, 10)
        for i, label in enumerate(["#", "Inventory", "QB", "QTY", "CLD", "D|D", "CMN"]):
            if i == 1:
                c.drawString(xs[i] + 2, top - 12, label)
            else:
                c.drawCentredString((xs[i] + xs[i + 1]) / 2, top - 12, label)
        for i, item in enumerate(page):
            sequence += 1
            y = top - header_height - row_height * (i + 1) + 3
            c.setFont(font, 9)
            c.drawRightString(xs[1] - 3, y, str(sequence))
            name = " ".join(item["name"].split())
            size = min(9.96, 9.96 * (xs[2] - xs[1] - 5) / pdfmetrics.stringWidth(name, font, 9.96))
            if size < 7.5:
                raise ValueError("Item name would be too small to print legibly")
            c.setFont(font, size)
            c.drawString(xs[1] + 2, y, name)
            value = format(Decimal(item["quantity"]), "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            size = min(9.96, 9.96 * (xs[3] - xs[2] - 4) / pdfmetrics.stringWidth(value, font, 9.96))
            if size < 7.5:
                raise ValueError("Quantity would be too small to print legibly")
            c.setFont(font, size)
            c.drawCentredString((xs[2] + xs[3]) / 2, y, value)
        c.setFont(font, 8)
        c.drawRightString(596, 24, f"Page {page_number} of {len(pages)}")
        c.showPage()
    c.save()
    return pages
