"""Bounded non-tax sales receipts: cash/check accounting, never payment processing."""

import copy
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from . import customer_payments, invoice_compatibility, invoice_receipt
from .bill_lookup import INVENTORY_FIELDS, validate_inventory_item
from .config import BridgeError, identifier, strict_keys
from .invoice_adjustments import subtotal
from .invoice_commercial import decimal_evidence
from .validation import digest, validate_invoice

INVOICE_ONLY = ("ARAccountRef", "AppliedAmount", "BalanceRemaining", "IsPaid", "IsFinanceCharge")
RECEIPT_FIELDS = tuple(
    field.replace("InvoiceLine", "SalesReceiptLine")
    for field in invoice_receipt.RECEIPT_FIELDS
    if field not in INVOICE_ONLY and field not in ("IsToBeEmailed", "LinkedTxn")
) + ("TotalAmount", "DepositToAccountRef", "PaymentMethodRef", "CheckNumber", "CreditCardTxnInfo")


def invoice_payload(payload):
    return {
        k: v for k, v in payload.items() if k not in ("deposit_id", "method_id", "check_number")
    }


def validate_payload(payload, policy):
    strict_keys(
        payload,
        {"customer_id", "deposit_id", "method_id", "txn_date", "ref_number", "currency", "lines"},
        {"tax_amount", "check_number"},
    )
    base = validate_invoice(invoice_payload(payload), policy)
    if Decimal(base.get("tax_amount", "0.00")) != 0:
        raise BridgeError("sales-receipt tax is outside the selected pilot")
    mappings = customer_payments.validate_masters(policy.payment_masters)
    for key, group in (("deposit_id", "deposits"), ("method_id", "methods")):
        identifier(payload[key])
        if payload[key] not in mappings.get(group, {}):
            raise BridgeError("sales-receipt deposit or method is not configured")
    if mappings.get("customers", {}).get(payload["customer_id"]) != policy.invoice_masters.get(
        "customers", {}
    ).get(payload["customer_id"]):
        raise BridgeError("sales and payment customer identities differ")
    if "check_number" in payload:
        value = payload["check_number"]
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 25
            or not value.isascii()
            or not value.isalnum()
        ):
            raise BridgeError("check number requires 1-25 ASCII letters or digits")
    return {
        **base,
        **{k: payload[k] for k in ("deposit_id", "method_id", "check_number") if k in payload},
    }


def plan(policy, payload):
    receipt = validate_payload(payload, policy)
    base = invoice_compatibility.plan(policy, invoice_payload(receipt))
    stock = invoice_receipt.inventory_specs(policy, invoice_payload(receipt))
    if stock:
        base["fields"] = {
            **base["fields"],
            "ItemInventory": tuple(
                dict.fromkeys((*base["fields"]["ItemInventory"], *INVENTORY_FIELDS))
            ),
        }
    if len(base["queries"]) > 85:
        raise BridgeError("sales-receipt master query limit exceeded")
    binding = {
        "customer": policy.invoice_masters["customers"][receipt["customer_id"]],
        "deposit": policy.payment_masters["deposits"][receipt["deposit_id"]],
        "method": policy.payment_masters["methods"][receipt["method_id"]],
    }
    return {
        "receipt": receipt,
        "master_plan": base,
        "binding": binding,
        "inventory": stock,
        "context_sha256": digest(
            {
                "schema": "sales-receipt-v1",
                "receipt": receipt,
                "masters": base["context_sha256"],
                "binding": binding,
            }
        ),
    }


def _query(batch, entity, run, key, fields, *, selector="ListID"):
    q = ET.SubElement(batch, entity + "QueryRq", requestID=run)
    ET.SubElement(q, selector).text = key
    if entity == "SalesReceipt":
        ET.SubElement(q, "IncludeLineItems").text = "true"
    for field in fields:
        ET.SubElement(q, "IncludeRetElement").text = field


def append_check(discovery, run, check):
    root = fromstring(invoice_compatibility.append_queries(discovery, run, check["master_plan"]))
    _query(
        root[0],
        "Account",
        run + "90",
        check["binding"]["deposit"],
        (*customer_payments.FIELDS["Account"], "Balance"),
    )
    _query(
        root[0],
        "PaymentMethod",
        run + "91",
        check["binding"]["method"],
        customer_payments.FIELDS["PaymentMethod"],
    )
    _query(
        root[0],
        "Customer",
        run + "92",
        check["binding"]["customer"],
        ("ListID", "IsActive", "Balance", "CurrencyRef"),
    )
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    count = 2 + len(check["master_plan"]["queries"])
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != count + 3
    ):
        raise BridgeError("sales-receipt check response set differs")
    responses = list(parse_response(xml))
    rows = []
    for rs, entity, suffix, key in zip(
        responses[count:],
        ("Account", "PaymentMethod", "Customer"),
        ("90", "91", "92"),
        (check["binding"]["deposit"], check["binding"]["method"], check["binding"]["customer"]),
        strict=True,
    ):
        if (
            rs.entity != entity
            or rs.request_id != run + suffix
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("sales-receipt master response is unsuccessful or uncorrelated")
        row = rs.records[0]
        if row.get("ListID") != key or row.get("IsActive") != "true" or "CurrencyRef" in row:
            raise BridgeError("sales-receipt master identity, activity or currency differs")
        rows.append(row)
    account, method, customer = rows
    if account.get("AccountType") != "Bank" and not (
        account.get("AccountType") == "OtherCurrentAsset"
        and account.get("SpecialAccountType") == "UndepositedFunds"
    ):
        raise BridgeError("sales-receipt deposit must be Bank or verified UndepositedFunds")
    method_type = method.get("PaymentMethodType")
    if method_type not in ("Cash", "Check") or (
        ("check_number" in check["receipt"]) != (method_type == "Check")
    ):
        raise BridgeError("sales receipt requires Cash or Check with an explicit check number")
    for node in list(root[0])[count:]:
        root[0].remove(node)
    base = copy.deepcopy(check["master_plan"])
    if recovering:
        base["skip_inventory_availability"] = True
    discovery = invoice_compatibility.validate_response(ET.tostring(root), run, base)
    stock = {}
    for rs in responses[:count]:
        if rs.entity == "ItemInventory":
            for row in rs.records:
                stock[row["ListID"]] = validate_inventory_item(
                    row, check["inventory"][row["ListID"]]
                )
    if set(stock) != set(check["inventory"]):
        raise BridgeError("complete sales-receipt stock evidence required")
    return discovery, {
        "deposit_balance": str(decimal_evidence(account.get("Balance"))),
        "customer_balance": str(decimal_evidence(customer.get("Balance"))),
        "stock": stock,
    }


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = fromstring(invoice_receipt.add_request(policy, invoice_payload(payload), run))
    rq, row = root[0][0], root[0][0][0]
    rq.tag, row.tag = "SalesReceiptAddRq", "SalesReceiptAdd"
    for node in list(row):
        if node.tag in INVOICE_ONLY or node.tag == "IsToBeEmailed":
            row.remove(node)
        elif node.tag == "InvoiceLineAdd":
            node.tag = "SalesReceiptLineAdd"
    pending = list(row).index(row.find("IsPending")) + 1
    if "check_number" in payload:
        node = ET.Element("CheckNumber")
        node.text = payload["check_number"]
        row.insert(pending, node)
        pending += 1
    method = ET.Element("PaymentMethodRef")
    ET.SubElement(method, "ListID").text = check["binding"]["method"]
    row.insert(pending, method)
    deposit = ET.Element("DepositToAccountRef")
    ET.SubElement(deposit, "ListID").text = check["binding"]["deposit"]
    row.insert(list(row).index(row.find("SalesReceiptLineAdd")), deposit)
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("select exactly one sales-receipt identity")
    root = fromstring(discovery)
    _query(
        root[0],
        "SalesReceipt",
        run,
        txn_id or ref_number,
        RECEIPT_FIELDS,
        selector="TxnID" if txn_id else "RefNumber",
    )
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_receipt(xml, policy, payload, run, *, operation="SalesReceiptQuery", txn_id=None):
    check = plan(policy, payload)
    root = fromstring(xml)
    if (
        operation not in ("SalesReceiptAdd", "SalesReceiptQuery")
        or root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 1
        or root[0][0].tag != operation + "Rs"
        or len(root[0][0]) != 1
        or root[0][0][0].tag != "SalesReceiptRet"
    ):
        raise BridgeError("exact saved sales receipt required")
    row = root[0][0][0]
    for path, expected in (
        ("DepositToAccountRef/ListID", check["binding"]["deposit"]),
        ("PaymentMethodRef/ListID", check["binding"]["method"]),
    ):
        parent, child = path.split("/")
        nodes = row.findall(parent)
        if (
            len(nodes) != 1
            or len(nodes[0].findall(child)) != 1
            or nodes[0].findtext(child) != expected
        ):
            raise BridgeError("saved sales-receipt deposit or payment method differs")
    amount = subtotal(invoice_payload(payload))
    if (
        len(row.findall("TotalAmount")) != 1
        or decimal_evidence(row.findtext("TotalAmount")) != amount
    ):
        raise BridgeError("saved sales-receipt total differs")
    checks = row.findall("CheckNumber")
    if (
        len(checks) > 1
        or (row.findtext("CheckNumber") or None) != payload.get("check_number")
        or row.find("CreditCardTxnInfo") is not None
    ):
        raise BridgeError("saved check number or payment processing data differs")
    # This internal adapter reuses exact saved item/price/quantity/tax validation.
    # TotalAmount and real cash/deposit references above remain authoritative.
    for tag in INVOICE_ONLY:
        if row.find(tag) is not None:
            raise BridgeError("unexpected invoice field in sales receipt")
    ET.SubElement(ET.SubElement(row, "ARAccountRef"), "ListID").text = policy.account_roles[
        "invoice_receivable"
    ]
    for tag, value in (
        ("AppliedAmount", "0"),
        ("BalanceRemaining", str(amount)),
        ("IsPaid", "false"),
        ("IsFinanceCharge", "false"),
    ):
        ET.SubElement(row, tag).text = value
    if row.find("IsToBeEmailed") is None:
        ET.SubElement(row, "IsToBeEmailed").text = "false"
    row.tag = "InvoiceRet"
    root[0][0].tag = operation.replace("SalesReceipt", "Invoice") + "Rs"
    for node in row:
        node.tag = node.tag.replace("SalesReceiptLine", "InvoiceLine")
    result = invoice_receipt.validate_receipt(
        ET.tostring(root),
        policy,
        invoice_payload(payload),
        run,
        operation=operation.replace("SalesReceipt", "Invoice"),
        txn_id=txn_id,
    )
    result.pop("balance_remaining")
    return {
        **result,
        "total_amount": format(amount, ".2f"),
        "deposit_list_id": check["binding"]["deposit"],
        "method_list_id": check["binding"]["method"],
        "verification": "matched-saved-sales-receipt",
    }


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid sales-receipt lookup envelope")
    saved = root[0][-1]
    root[0].remove(saved)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=True
    )
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(saved)
    receipt = validate_receipt(ET.tostring(isolated), policy, payload, run + "99", txn_id=txn_id)
    return discovery, {**receipt, "balances": balances}


def verify_balance_effect(payload, before, after, *, policy):
    total = subtotal(invoice_payload(payload))
    if decimal_evidence(before.get("deposit_balance")) + total != decimal_evidence(
        after.get("deposit_balance")
    ) or decimal_evidence(before.get("customer_balance")) != decimal_evidence(
        after.get("customer_balance")
    ):
        raise BridgeError("sales-receipt bank/customer balance effect differs; never resend")
    expected = plan(policy, payload)["inventory"]
    if set(before.get("stock", {})) != set(expected) or set(after.get("stock", {})) != set(
        expected
    ):
        raise BridgeError("original sales-receipt stock baseline required")
    effects = {}
    for key in expected:
        quantity = sum(
            Decimal(line["quantity"])
            for line in payload["lines"]
            if policy.invoice_masters["items"][line["item_id"]]["list_id"] == key
        )
        old, new = before["stock"][key], after["stock"][key]
        if decimal_evidence(old["quantity_on_hand"]) - quantity != decimal_evidence(
            new["quantity_on_hand"]
        ) or decimal_evidence(old["average_cost"]) != decimal_evidence(new["average_cost"]):
            raise BridgeError("sales-receipt stock/cost effect differs; never resend")
        effects[key] = {
            "before": old["quantity_on_hand"],
            "sold": str(quantity),
            "after": new["quantity_on_hand"],
        }
    return {
        "deposit_before": before["deposit_balance"],
        "received": format(total, ".2f"),
        "deposit_after": after["deposit_balance"],
        "customer_balance_unchanged": after["customer_balance"],
        "stock_effects": effects,
    }
