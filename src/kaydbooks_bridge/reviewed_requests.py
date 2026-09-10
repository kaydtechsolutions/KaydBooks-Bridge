"""Requests and strict saved-field comparison for explicitly reviewed entries.

This module never dispatches XML. References resolve only through private mappings.
"""

from decimal import Decimal
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring

from .config import BridgeError

KINDS = {
    "invoice.create": "Invoice",
    "sales-receipt.create": "SalesReceipt",
    "customer-payment.create": "ReceivePayment",
    "check.create": "Check",
    "journal.create": "JournalEntry",
    "customer.create": "Customer",
}


def render(root):
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + E.tostring(root, encoding="unicode")


def mapping(maps, group, name):
    value = maps[group][name]
    if not value.get("ListID") or value.get("IsActive") != "true":
        raise BridgeError("missing or inactive reviewed master")
    return value["ListID"]


def build(entry, maps, run):
    kind = KINDS[entry["operation"]]
    root = E.Element("QBXML")
    batch = E.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    row = E.SubElement(E.SubElement(batch, kind + "AddRq", requestID=run), kind + "Add")

    def text(parent, tag, value):
        E.SubElement(parent, tag).text = str(value)

    def ref(parent, tag, group, name):
        if group == "customers" and maps[group][name].get("authorized_create") is True:
            text(E.SubElement(parent, tag), "FullName", name)
            return
        text(E.SubElement(parent, tag), "ListID", mapping(maps, group, name))

    if kind == "Customer":
        text(row, "Name", entry["name"])
        text(row, "IsActive", "true")
        return render(root)
    if kind in ("Invoice", "SalesReceipt", "ReceivePayment"):
        ref(row, "CustomerRef", "customers", entry["customer_full_name"])
    if kind in ("Invoice", "ReceivePayment"):
        text(E.SubElement(row, "ARAccountRef"), "ListID", maps["receivable_id"])
    if kind == "Check":
        ref(row, "AccountRef", "accounts", entry["bank_account_full_name"])
        ref(row, "PayeeEntityRef", "vendors", entry["payee_full_name"])
        text(row, "RefNumber", entry["source_reference"])
        text(row, "TxnDate", entry["txn_date"])
        text(row, "Memo", entry["check_memo"])
        text(row, "IsToBePrinted", "false")
        for line in entry["lines"]:
            node = E.SubElement(row, "ExpenseLineAdd")
            ref(node, "AccountRef", "accounts", line["expense_account_full_name"])
            text(node, "Amount", format(Decimal(line["amount"]), ".2f"))
        return render(root)
    text(row, "TxnDate", entry["txn_date"])
    text(row, "RefNumber", entry["source_reference"])
    if kind == "JournalEntry":
        text(row, "IsAdjustment", str(entry["adjusting_entry"]).lower())
        for line in entry["lines"]:
            node = E.SubElement(
                row, "JournalDebitLine" if line["side"] == "debit" else "JournalCreditLine"
            )
            ref(node, "AccountRef", "accounts", line["account_full_name"])
            text(node, "Amount", format(Decimal(line["amount"]), ".2f"))
            text(node, "Memo", line["memo"])
            ref(node, "EntityRef", "names", entry["name_full_name"])
        return render(root)
    if kind == "ReceivePayment":
        text(row, "TotalAmount", entry["total_amount"])
        ref(row, "PaymentMethodRef", "payment_methods", entry["payment_method"])
        ref(row, "DepositToAccountRef", "accounts", entry["deposit_account_full_name"])
        for line in entry["allocations"]:
            node = E.SubElement(row, "AppliedToTxnAdd")
            text(node, "TxnID", maps["invoices"][line["invoice_ref_number"]]["TxnID"])
            text(node, "PaymentAmount", format(Decimal(line["payment_amount"]), ".2f"))
            if "discount_amount" in line:
                text(node, "DiscountAmount", format(Decimal(line["discount_amount"]), ".2f"))
                ref(node, "DiscountAccountRef", "accounts", line["discount_account_full_name"])
        return render(root)
    text(row, "IsPending", "false")
    if kind == "Invoice":
        text(row, "IsFinanceCharge", "false")
        if entry.get("terms_full_name"):
            ref(row, "TermsRef", "terms", entry["terms_full_name"])
    else:
        ref(row, "PaymentMethodRef", "payment_methods", entry["payment_method"])
    ref(row, "SalesRepRef", "sales_reps", entry["sales_rep_initials"])
    text(row, "IsToBePrinted", "false")
    if kind == "Invoice":
        text(row, "IsToBeEmailed", "false")
    text(E.SubElement(row, "CustomerSalesTaxCodeRef"), "ListID", maps["non_tax_code_id"])
    if kind == "SalesReceipt":
        ref(row, "DepositToAccountRef", "accounts", entry["deposit_account_full_name"])
    for line in entry["lines"]:
        node = E.SubElement(row, kind + "LineAdd")
        ref(node, "ItemRef", "items", line["item_full_name"])
        if "description" in line:
            text(node, "Desc", line["description"])
        text(node, "Quantity", str(Decimal(line["quantity"])))
        text(node, "Rate", str(Decimal(line["unit_price"])))
        text(node, "Amount", format(Decimal(line["amount"]), ".2f"))
        ref(node, "InventorySiteRef", "inventory_sites", entry["inventory_site_full_name"])
        text(E.SubElement(node, "SalesTaxCodeRef"), "ListID", maps["non_tax_code_id"])
    return render(root)


NUMBERS = {"Quantity", "Rate", "Amount", "TotalAmount", "PaymentAmount", "DiscountAmount"}


def compare(request_row, saved_row):
    """Every explicitly supplied field must survive readback, including repeated lines."""
    grouped = {}
    for node in request_row:
        grouped.setdefault(node.tag, []).append(node)
    for tag, expected in grouped.items():
        saved_tag = tag[:-3] + "Ret" if tag.endswith("Add") else tag
        if request_row.tag == "AppliedToTxnAdd" and tag == "PaymentAmount":
            saved_tag = "Amount"
        actual = saved_row.findall(saved_tag)
        if len(actual) != len(expected):
            raise BridgeError("saved field count differs: " + saved_tag)
        for left, right in zip(expected, actual, strict=True):
            if len(left):
                compare(left, right)
            elif tag in NUMBERS:
                if right.text is None or Decimal(left.text) != Decimal(right.text):
                    raise BridgeError("saved numeric field differs: " + tag)
            elif left.text != right.text:
                raise BridgeError("saved reviewed field differs: " + tag)


def verify(entry, maps, saved):
    if saved.tag != KINDS[entry["operation"]] + "Ret":
        raise BridgeError("saved record type differs")
    expected = fromstring(build(entry, maps, "1"))[0][0][0]
    compare(expected, saved)
    kind = KINDS[entry["operation"]]
    identity = saved.findtext("ListID" if kind == "Customer" else "TxnID")
    if not identity or not saved.findtext("EditSequence"):
        raise BridgeError("saved record identity missing")
    if kind == "ReceivePayment":
        if Decimal(saved.findtext("UnusedPayment", "NaN")) != 0:
            raise BridgeError("unexpected unused payment")
        if any(x.findtext("TxnType") != "Invoice" for x in saved.findall("AppliedToTxnRet")):
            raise BridgeError("payment applied to unexpected transaction type")
    if kind in ("Invoice", "SalesReceipt"):
        if Decimal(saved.findtext("SalesTaxTotal", "NaN")) != 0:
            raise BridgeError("saved tax is not zero")
        total = "Subtotal" if kind == "Invoice" else "TotalAmount"
        if Decimal(saved.findtext(total, "NaN")) != Decimal(entry["total_amount"]):
            raise BridgeError("saved transaction total differs")
        if kind == "Invoice" and Decimal(saved.findtext("AppliedAmount", "NaN")) != 0:
            raise BridgeError("unexpected invoice application")
        if (
            kind == "Invoice"
            and not entry.get("terms_full_name")
            and saved.find("TermsRef") is not None
        ):
            raise BridgeError("unexpected inherited terms on reviewed no-term invoice")
    if kind == "Check" and Decimal(saved.findtext("Amount", "NaN")) != Decimal(
        entry["total_amount"]
    ):
        raise BridgeError("saved check total differs")
    return identity
