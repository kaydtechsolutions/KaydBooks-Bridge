"""Non-tax expense/service supplier credits tied to an original bill."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from . import bill_lookup, bill_receipt, bills
from .config import BridgeError
from .customer_credits import _record
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .validation import digest

VENDOR_FIELDS = ("ListID", "IsActive", "Balance", "CurrencyRef")
SOURCE_FIELDS = (
    "TxnID",
    "EditSequence",
    "VendorRef",
    "APAccountRef",
    "TxnDate",
    "RefNumber",
    "AmountDue",
    "IsTaxIncluded",
    "SalesTaxCodeRef",
    "CurrencyRef",
    "ExchangeRate",
    "ExpenseLineRet",
    "ItemLineRet",
    "ItemGroupLineRet",
)
CREDIT_FIELDS = tuple("CreditAmount" if f == "AmountDue" else f for f in SOURCE_FIELDS) + (
    "Memo",
    "LinkedTxn",
    "OpenAmount",
)


def bill_payload(payload):
    if not isinstance(payload, dict) or "bill_txn_id" not in payload:
        raise BridgeError("supplier credit requires original bill identity")
    required_id(payload["bill_txn_id"])
    if "due_date" in payload or "terms_id" in payload:
        raise BridgeError("supplier credit has no due date or payment terms")
    return {
        **{k: v for k, v in payload.items() if k != "bill_txn_id"},
        "due_date": payload.get("txn_date"),
    }


def validate_payload(payload, policy):
    base = bills.validate_payload(bill_payload(payload), policy)
    base.pop("due_date")
    return {**base, "bill_txn_id": payload["bill_txn_id"]}


def memo(payload):
    return "KaydBooks bill " + required_id(payload["bill_txn_id"])


def plan(policy, payload):
    credit = validate_payload(payload, policy)
    masters = bill_lookup.plan(policy, bill_payload(credit))
    if len(masters["queries"]) > 87:
        raise BridgeError("supplier credit master query limit exceeded")
    return {
        "credit": credit,
        "master_plan": masters,
        "vendor": masters["binding"]["vendor_list_id"],
        "payable": masters["binding"]["payable_list_id"],
        "context_sha256": digest(
            {"schema": "supplier-credit-v1", "credit": credit, "masters": masters["context_sha256"]}
        ),
    }


def _query(batch, entity, run, selector, fields):
    q = ET.SubElement(batch, entity + "QueryRq", requestID=bill_receipt.correlation(run))
    if isinstance(selector, list):
        ET.SubElement(ET.SubElement(q, "EntityFilter"), "ListID").text = selector[0]
    else:
        ET.SubElement(q, "ListID" if entity == "Vendor" else "TxnID").text = selector
    if entity != "Vendor":
        ET.SubElement(q, "IncludeLineItems").text = "true"
    for field in fields:
        ET.SubElement(q, "IncludeRetElement").text = field
    return q


def append_check(discovery, run, check):
    root = fromstring(bill_lookup.append_check(discovery, run, check["master_plan"]))
    _query(root[0], "Vendor", run + "90", check["vendor"], VENDOR_FIELDS)
    _query(root[0], "Bill", run + "91", check["credit"]["bill_txn_id"], SOURCE_FIELDS)
    _query(root[0], "VendorCredit", run + "92", [check["vendor"]], CREDIT_FIELDS)
    q = ET.SubElement(root[0], "BillToPayQueryRq", requestID=run + "93")
    ET.SubElement(ET.SubElement(q, "PayeeEntityRef"), "ListID").text = check["vendor"]
    ET.SubElement(ET.SubElement(q, "APAccountRef"), "ListID").text = check["payable"]
    return bill_receipt.render(root)


def _lines(row):
    if (
        any(k in row for k in ("ItemGroupLineRet", "CurrencyRef", "SalesTaxCodeRef"))
        or row.get("IsTaxIncluded", "false") != "false"
        or decimal_evidence(row.get("ExchangeRate", "1")) != 1
    ):
        raise BridgeError("unsupported supplier credit source feature")
    result, seen = {}, set()
    for kind, ref in (("ExpenseLineRet", "AccountRef"), ("ItemLineRet", "ItemRef")):
        lines = row.get(kind, [])
        if isinstance(lines, dict):
            lines = [lines]
        if not isinstance(lines, list) or len(lines) > 100:
            raise BridgeError("bounded credit lines required")
        for line in lines:
            if not isinstance(line, dict) or not isinstance(line.get(ref), dict):
                raise BridgeError("supplier credit line identity missing")
            key = (kind, required_id(line[ref].get("ListID")))
            line_id = required_id(line.get("TxnLineID"))
            if (
                line_id in seen
                or any(
                    f in line
                    for f in (
                        "UnitOfMeasure",
                        "OverrideUOMSetRef",
                        "InventorySiteRef",
                        "SerialNumber",
                        "LotNumber",
                        "SalesTaxCodeRef",
                        "CustomerRef",
                        "ClassRef",
                    )
                )
                or decimal_evidence(line.get("TaxAmount", "0")) != 0
            ):
                raise BridgeError("ambiguous or unsupported supplier credit line")
            seen.add(line_id)
            amount = decimal_evidence(line.get("Amount"))
            qty = decimal_evidence(line.get("Quantity")) if kind == "ItemLineRet" else Decimal(0)
            if amount <= 0 or (kind == "ItemLineRet" and qty <= 0):
                raise BridgeError("positive supplier credit lines required")
            old = result.get(key, (Decimal(0), Decimal(0)))
            result[key] = old[0] + amount, old[1] + qty
    if not result:
        raise BridgeError("supplier credit lines missing")
    return result


def payable_rows(rs, run, payable):
    rows = _record(rs, "BillToPay", run, many=True)
    result = {}
    for entry in rows:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise BridgeError("ambiguous payable evidence")
        kind, row = next(iter(entry.items()))
        if kind not in ("BillToPay", "CreditToApply") or not isinstance(row, dict):
            raise BridgeError("invalid payable evidence")
        key = required_id(row.get("TxnID"))
        if (
            key in result
            or row.get("APAccountRef", {}).get("ListID") != payable
            or "CurrencyRef" in row
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
        ):
            raise BridgeError("payable identity or currency differs")
        amount = decimal_evidence(
            row.get("AmountDue" if kind == "BillToPay" else "CreditRemaining")
        )
        if amount < 0:
            raise BridgeError("negative payable evidence")
        result[key] = {"kind": kind, "amount": str(amount), "txn_type": row.get("TxnType")}
    return result


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    count = 2 + len(check["master_plan"]["queries"])
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != count + 4
    ):
        raise BridgeError("supplier credit response set differs")
    responses = list(parse_response(xml))
    vendor = _record(responses[-4], "Vendor", run + "90")
    bill = _record(responses[-3], "Bill", run + "91")
    credits = _record(responses[-2], "VendorCredit", run + "92", many=True)
    if any(n.get("iteratorRemainingCount") not in (None, "0") for n in list(root[0])[-2:]):
        raise BridgeError("complete credit and payable evidence required")
    if (
        vendor.get("ListID") != check["vendor"]
        or vendor.get("IsActive") != "true"
        or "CurrencyRef" in vendor
    ):
        raise BridgeError("supplier credit vendor differs")
    balance = decimal_evidence(vendor.get("Balance"))
    if bill.get("TxnID") != check["credit"]["bill_txn_id"]:
        raise BridgeError("supplier credit source bill differs")
    for field, key in (("VendorRef", "vendor"), ("APAccountRef", "payable")):
        if not isinstance(bill.get(field), dict) or bill[field].get("ListID") != check[key]:
            raise BridgeError("source bill vendor or AP differs")
    required_id(bill.get("EditSequence"))
    try:
        if (
            date.fromisoformat(bill["TxnDate"]).isoformat() != bill["TxnDate"]
            or bill["TxnDate"] > check["credit"]["txn_date"]
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise BridgeError("source bill date invalid") from exc
    capacities = _lines(bill)
    if sum(a for a, _ in capacities.values()) != decimal_evidence(bill.get("AmountDue")):
        raise BridgeError("complete original bill lines required")
    used, seen = {}, set()
    for row in credits:
        key = required_id(row.get("TxnID"))
        if key in seen or row.get("VendorRef", {}).get("ListID") != check["vendor"]:
            raise BridgeError("ambiguous supplier credit history")
        seen.add(key)
        if row.get("Memo") != memo(check["credit"]):
            continue
        if recovering and row.get("RefNumber") == check["credit"]["ref_number"]:
            continue
        if row.get("APAccountRef", {}).get("ListID") != check["payable"]:
            raise BridgeError("prior supplier credit AP differs")
        prior_lines = _lines(row)
        if sum(a for a, _ in prior_lines.values()) != decimal_evidence(row.get("CreditAmount")):
            raise BridgeError("complete prior supplier credit lines required")
        for key, (amount, qty) in prior_lines.items():
            old = used.get(key, (Decimal(0), Decimal(0)))
            used[key] = old[0] + amount, old[1] + qty
    binding = check["master_plan"]["binding"]
    for i, line in enumerate(check["credit"]["lines"]):
        key = (
            ("ItemLineRet", binding["item_list_ids"][i])
            if "item_id" in line
            else ("ExpenseLineRet", binding["expense_list_ids"][i])
        )
        old = used.get(key, (Decimal(0), Decimal(0)))
        used[key] = old[0] + Decimal(line["amount"]), old[1] + Decimal(line.get("quantity", "0"))
    if any(
        key not in capacities or amount > capacities[key][0] or qty > capacities[key][1]
        for key, (amount, qty) in used.items()
    ):
        raise BridgeError("supplier credit exceeds original bill after prior Bridge-linked credits")
    payables = payable_rows(responses[-1], run + "93", check["payable"])
    for n in list(root[0])[count:]:
        root[0].remove(n)
    discovery = bill_lookup.validate_check(ET.tostring(root), run, check["master_plan"])
    inventory = binding.get("inventory_items", {})
    stock = {}
    if inventory:
        for rs in responses[:count]:
            if rs.entity != "ItemInventory":
                continue
            for item in rs.records:
                item_id = item["ListID"]
                observation = bill_lookup.validate_inventory_item(item, inventory[item_id])
                quantities = [
                    line
                    for index, line in enumerate(check["credit"]["lines"])
                    if binding["item_list_ids"][index] == item_id
                ]
                required = sum(Decimal(line["quantity"]) for line in quantities)
                if not recovering and (
                    decimal_evidence(observation["quantity_on_hand"]) < required
                    or any(
                        Decimal(line["cost"]) != decimal_evidence(observation["average_cost"])
                        for line in quantities
                    )
                ):
                    raise BridgeError(
                        "inventory return requires available stock at verified average cost"
                    )
                stock[item_id] = {**observation, "return_quantity": str(required)}
        if set(stock) != set(inventory):
            raise BridgeError("complete inventory return stock required")
    return discovery, {
        **({"stock": stock} if inventory else {}),
        "vendor_balance": str(balance),
        "source_bill": bill["TxnID"],
        "payables": payables,
        "limit_scope": "credits with the same Bridge source-bill memo",
    }


def add_request(policy, payload, run):
    validate_payload(payload, policy)
    root = fromstring(bill_receipt.add_request(policy, bill_payload(payload), run))
    root[0][0].tag = "VendorCreditAddRq"
    add = root[0][0][0]
    add.tag = "VendorCreditAdd"
    add.remove(add.find("DueDate"))
    node = ET.Element("Memo")
    node.text = memo(payload)
    add.insert(4, node)
    return bill_receipt.render(root)


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("select one supplier credit identity")
    root = fromstring(discovery)
    q = _query(
        root[0],
        "VendorCredit",
        run,
        required_id(txn_id) if txn_id else "placeholder",
        CREDIT_FIELDS,
    )
    if ref_number is not None:
        q[0].tag, q[0].text = "RefNumber", ref_number
    return bill_receipt.render(root)


def lookup_context(policy, payload, txn_id):
    return digest(
        {
            "schema": "supplier-credit-outcome-v1",
            "context": plan(policy, payload)["context_sha256"],
            "txn_id": required_id(txn_id),
        }
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_receipt(xml, policy, payload, run, *, operation="VendorCreditQuery", txn_id=None):
    if operation not in ("VendorCreditAdd", "VendorCreditQuery"):
        raise BridgeError("invalid supplier credit operation")
    root = fromstring(xml)
    if (
        len(root) != 1
        or len(root[0]) != 1
        or root[0][0].tag != operation + "Rs"
        or len(root[0][0]) != 1
        or root[0][0][0].tag != "VendorCreditRet"
    ):
        raise BridgeError("exact supplier credit receipt required")
    row = root[0][0][0]
    if (
        len(row.findall("Memo")) != 1
        or row.findtext("Memo") != memo(payload)
        or len(row.findall("CreditAmount")) != 1
        or row.find("LinkedTxn") is not None
    ):
        raise BridgeError("supplier credit source or application differs")
    # Reuse the exact expense/service comparator, not bill outstanding semantics.
    root[0][0].tag = operation.replace("VendorCredit", "Bill") + "Rs"
    row.tag = "BillRet"
    row.find("CreditAmount").tag = "AmountDue"
    for name, value in (("DueDate", payload["txn_date"]), ("IsPaid", "false")):
        if row.find(name) is not None:
            raise BridgeError("unexpected bill field in credit")
        ET.SubElement(row, name).text = value
    result = bill_receipt.validate_receipt(
        ET.tostring(root),
        policy,
        bill_payload(payload),
        run,
        operation=operation.replace("VendorCredit", "Bill"),
        txn_id=txn_id,
    )
    result.pop("observed_billret_open_amount")
    result.update(
        operation="supplier-credit.create",
        source_bill=payload["bill_txn_id"],
        verification="matched-saved-supplier-credit",
        scope="unapplied-inventory-credit"
        if plan(policy, payload)["master_plan"]["binding"].get("inventory_items")
        else "unapplied-expense-service-credit",
        balance_verification="requires-credit-to-apply",
    )
    return result


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    credit = root[0][-1]
    root[0].remove(credit)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=True
    )
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(credit)
    receipt = validate_receipt(ET.tostring(isolated), policy, payload, run + "99", txn_id=txn_id)
    saved = balances["payables"].get(txn_id)
    if (
        saved is None
        or saved["kind"] != "CreditToApply"
        or saved["txn_type"] != "VendorCredit"
        or Decimal(saved["amount"]) != Decimal(receipt["total"])
    ):
        raise BridgeError("supplier credit unused amount differs in payable evidence")
    return discovery, {
        **receipt,
        "balance_verification": "matched-credit-to-apply",
        "unused_credit": saved["amount"],
        "balances": balances,
    }


def verify_balance_effect(payload, before, after):
    if (
        not isinstance(before, dict)
        or before.get("source_bill") != payload["bill_txn_id"]
        or after.get("source_bill") != payload["bill_txn_id"]
    ):
        raise BridgeError("original supplier credit baseline required")
    total = sum(Decimal(line["amount"]) for line in payload["lines"])

    def net(rows):
        return sum(
            Decimal(row["amount"]) * (1 if row["kind"] == "BillToPay" else -1)
            for row in rows.values()
        )

    if decimal_evidence(before.get("vendor_balance")) - total != decimal_evidence(
        after.get("vendor_balance")
    ) or net(before["payables"]) - total != net(after["payables"]):
        raise BridgeError("supplier credit vendor/payable balance effect differs; never resend")
    stock_effects = {}
    if "stock" in before or "stock" in after:
        if not isinstance(before.get("stock"), dict) or set(before["stock"]) != set(
            after.get("stock", {})
        ):
            raise BridgeError("inventory return baseline or observation missing")
        for key, observed in before["stock"].items():
            returned = decimal_evidence(observed["return_quantity"])
            prior = decimal_evidence(observed["quantity_on_hand"])
            current = decimal_evidence(after["stock"][key]["quantity_on_hand"])
            if (
                returned <= 0
                or prior < returned
                or current != prior - returned
                or decimal_evidence(after["stock"][key]["return_quantity"]) != returned
            ):
                raise BridgeError("inventory return stock effect differs; never resend")
            stock_effects[key] = {
                "before": str(prior),
                "returned": str(returned),
                "after": str(current),
                "verification": "matched-native-stock-decrease",
            }
    return {
        **({"stock_effects": stock_effects} if stock_effects else {}),
        "before": before["vendor_balance"],
        "credit": str(total),
        "after": after["vendor_balance"],
        "payable_before": str(net(before["payables"])),
        "payable_after": str(net(after["payables"])),
    }
