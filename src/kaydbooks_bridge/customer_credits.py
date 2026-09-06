"""Non-tax service credit notes tied to a specific source invoice; no automatic application."""

from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import append_queries, required_id
from .invoice_compatibility import validate_response as validate_masters
from .invoice_receipt import RECEIPT_FIELDS, _check, _request_id
from .validation import digest, validate_invoice

CUSTOMER_FIELDS = ("ListID", "IsActive", "Balance", "CurrencyRef")
SOURCE_FIELDS = (
    "TxnID",
    "EditSequence",
    "CustomerRef",
    "ARAccountRef",
    "TxnDate",
    "RefNumber",
    "IsPending",
    "Subtotal",
    "SalesTaxTotal",
    "CurrencyRef",
    "ExchangeRate",
    "InvoiceLineRet",
    "InvoiceLineGroupRet",
)
CREDIT_FIELDS = tuple(
    f.replace("Invoice", "CreditMemo")
    for f in RECEIPT_FIELDS
    if f not in ("IsFinanceCharge", "IsPaid", "AppliedAmount", "BalanceRemaining")
) + ("TotalAmount", "CreditRemaining", "Memo")


def invoice_payload(payload):
    if not isinstance(payload, dict) or "invoice_txn_id" not in payload:
        raise BridgeError("credit note requires original invoice identity")
    required_id(payload["invoice_txn_id"])
    return {key: value for key, value in payload.items() if key != "invoice_txn_id"}


def validate_payload(payload, policy):
    base = validate_invoice(invoice_payload(payload), policy)
    check = _check(policy, base)
    if any(s.get("kind", "Service") != "Service" for s in check["item_specs"]):
        raise BridgeError("credit qualification currently supports service items only")
    return {**base, "invoice_txn_id": payload["invoice_txn_id"]}


def memo(payload):
    return "KaydBooks invoice " + required_id(payload["invoice_txn_id"])


def plan(policy, payload):
    credit = validate_payload(payload, policy)
    check = _check(policy, invoice_payload(credit))
    return {
        "credit": credit,
        "master_plan": check,
        "customer": policy.invoice_masters["customers"][credit["customer_id"]],
        "receivable": policy.account_roles["invoice_receivable"],
        "item_ids": {a: v["list_id"] for a, v in policy.invoice_masters["items"].items()},
        "context_sha256": digest(
            {
                "schema": "service-credit-check-v1",
                "credit": credit,
                "masters": check["context_sha256"],
            }
        ),
    }


def _query(batch, entity, run, selector, fields, lines=False):
    q = ET.SubElement(batch, entity + "QueryRq", requestID=run)
    if isinstance(selector, str):
        ET.SubElement(q, "ListID" if entity == "Customer" else "TxnID").text = selector
    else:
        ET.SubElement(ET.SubElement(q, "EntityFilter"), "ListID").text = selector[0]
    if lines:
        ET.SubElement(q, "IncludeLineItems").text = "true"
    for field in fields:
        ET.SubElement(q, "IncludeRetElement").text = field
    return q


def append_check(discovery, run, check):
    _request_id(run)
    root = fromstring(append_queries(discovery, run, check["master_plan"]))
    _query(root[0], "Customer", run + "90", check["customer"], CUSTOMER_FIELDS)
    _query(root[0], "Invoice", run + "91", check["credit"]["invoice_txn_id"], SOURCE_FIELDS, True)
    _query(root[0], "CreditMemo", run + "92", [check["customer"]], CREDIT_FIELDS, True)
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def _record(rs, entity, run, *, many=False):
    if rs.entity != entity or rs.request_id != run or len(rs.records) > (1000 if many else 1):
        raise BridgeError("credit response identity or cardinality differs")
    empty = (
        many
        and not rs.records
        and (rs.status_code, rs.status_severity) in ((1, "Info"), (500, "Warn"))
    )
    if not empty and (
        rs.status_code != 0 or rs.status_severity != "Info" or (not many and len(rs.records) != 1)
    ):
        raise BridgeError("credit response is incomplete or unsuccessful")
    return rs.records if many else rs.records[0]


def _lines(row, key):
    lines = row.get(key, [])
    if isinstance(lines, dict):
        lines = [lines]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise BridgeError("bounded original or credited lines required")
    result, seen = {}, set()
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("ItemRef"), dict):
            raise BridgeError("credit source item identity missing")
        key = required_id(line["ItemRef"].get("ListID"))
        line_id = required_id(line.get("TxnLineID"))
        if line_id in seen or any(
            field in line for field in ("UnitOfMeasure", "OverrideUOMSetRef", "RatePercent")
        ):
            raise BridgeError("ambiguous or unsupported original credit line")
        seen.add(line_id)
        amount, quantity = (
            decimal_evidence(line.get("Amount")),
            decimal_evidence(line.get("Quantity")),
        )
        if amount < 0 or quantity <= 0:
            raise BridgeError("unsupported source or prior credit line")
        prior = result.get(key, (Decimal(0), Decimal(0)))
        result[key] = (prior[0] + amount, prior[1] + quantity)
    return result


def validate_check(xml, run, check, *, recovering=False):
    root = fromstring(xml)
    count = 2 + len(check["master_plan"]["queries"])
    if len(root) != 1 or len(root[0]) != count + 3:
        raise BridgeError("credit check response set differs")
    responses = list(parse_response(xml))
    customer = _record(responses[-3], "Customer", run + "90")
    invoice = _record(responses[-2], "Invoice", run + "91")
    credits = _record(responses[-1], "CreditMemo", run + "92", many=True)
    if root[0][-1].get("iteratorRemainingCount") not in (None, "0"):
        raise BridgeError("complete customer credit history required")
    if (
        customer.get("ListID") != check["customer"]
        or customer.get("IsActive") != "true"
        or "CurrencyRef" in customer
    ):
        raise BridgeError("credit customer identity or currency differs")
    balance = decimal_evidence(customer.get("Balance"))
    if (
        invoice.get("TxnID") != check["credit"]["invoice_txn_id"]
        or invoice.get("IsPending") != "false"
        or "InvoiceLineGroupRet" in invoice
        or "CurrencyRef" in invoice
        or decimal_evidence(invoice.get("ExchangeRate", "1")) != 1
    ):
        raise BridgeError("unsupported original invoice")
    for field, key in (("CustomerRef", "customer"), ("ARAccountRef", "receivable")):
        if not isinstance(invoice.get(field), dict) or invoice[field].get("ListID") != check[key]:
            raise BridgeError("credit source invoice customer or AR differs")
    from datetime import date

    try:
        if (
            date.fromisoformat(invoice["TxnDate"]).isoformat() != invoice["TxnDate"]
            or invoice["TxnDate"] > check["credit"]["txn_date"]
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise BridgeError("credit source invoice date invalid") from exc
    capacities = _lines(invoice, "InvoiceLineRet")
    required_id(invoice.get("EditSequence"))
    if (
        sum(amount for amount, _ in capacities.values())
        != decimal_evidence(invoice.get("Subtotal"))
        or decimal_evidence(invoice.get("SalesTaxTotal")) != 0
    ):
        raise BridgeError("complete non-tax source invoice required")
    used, seen = {}, set()
    for row in credits:
        key = required_id(row.get("TxnID"))
        if (
            key in seen
            or not isinstance(row.get("CustomerRef"), dict)
            or row["CustomerRef"].get("ListID") != check["customer"]
        ):
            raise BridgeError("ambiguous credit history")
        seen.add(key)
        if row.get("Memo") != memo(check["credit"]):
            continue
        if recovering and row.get("RefNumber") == check["credit"]["ref_number"]:
            continue  # The separate exact saved-record check verifies this outcome.
        if (
            row.get("IsPending") != "false"
            or "CreditMemoLineGroupRet" in row
            or "CurrencyRef" in row
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
            or not isinstance(row.get("ARAccountRef"), dict)
            or row["ARAccountRef"].get("ListID") != check["receivable"]
            or decimal_evidence(row.get("SalesTaxTotal")) != 0
        ):
            raise BridgeError("unsupported prior credit state")
        prior_lines = _lines(row, "CreditMemoLineRet")
        if sum(amount for amount, _ in prior_lines.values()) != decimal_evidence(
            row.get("TotalAmount")
        ):
            raise BridgeError("complete prior credit lines required")
        for item, (amount, quantity) in prior_lines.items():
            prior = used.get(item, (Decimal(0), Decimal(0)))
            used[item] = (prior[0] + amount, prior[1] + quantity)
    for line in check["credit"]["lines"]:
        key = check["item_ids"][line["item_id"]]
        prior = used.get(key, (Decimal(0), Decimal(0)))
        used[key] = (prior[0] + Decimal(line["amount"]), prior[1] + Decimal(line["quantity"]))
    if any(
        key not in capacities or amount > capacities[key][0] or qty > capacities[key][1]
        for key, (amount, qty) in used.items()
    ):
        raise BridgeError(
            "credit exceeds original invoice item quantity or amount after prior Bridge-linked credits"
        )
    for n in list(root[0])[count:]:
        root[0].remove(n)
    discovery = validate_masters(ET.tostring(root), run, check["master_plan"])
    return discovery, {
        "customer_balance": str(balance),
        "source_invoice": invoice["TxnID"],
        "prior_credit_count": len(credits),
        "limit_scope": "credits with the same Bridge source-invoice memo",
    }


def add_request(policy, payload, run):
    from .invoice_receipt import add_request as invoice_request

    validate_payload(payload, policy)
    root = fromstring(invoice_request(policy, invoice_payload(payload), run))
    root[0][0].tag = "CreditMemoAddRq"
    add = root[0][0][0]
    add.tag = "CreditMemoAdd"
    add.remove(add.find("IsFinanceCharge"))
    n = ET.Element("Memo")
    n.text = memo(payload)
    add.insert(list(add).index(add.find("IsToBePrinted")), n)
    for line in add.findall("InvoiceLineAdd"):
        line.tag = "CreditMemoLineAdd"
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("select one credit identity")
    root = fromstring(discovery)
    q = _query(
        root[0],
        "CreditMemo",
        run,
        required_id(txn_id) if txn_id else "placeholder",
        CREDIT_FIELDS,
        True,
    )
    if ref_number is not None:
        q[0].tag = "RefNumber"
        q[0].text = ref_number
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def lookup_context(policy, payload, txn_id):
    return digest(
        {
            "schema": "service-credit-receipt-v1",
            "plan": plan(policy, payload)["context_sha256"],
            "txn_id": required_id(txn_id),
        }
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_receipt(xml, policy, payload, run, *, operation="CreditMemoQuery", txn_id=None):
    from .invoice_receipt import validate_receipt as invoice_receipt

    if operation not in ("CreditMemoAdd", "CreditMemoQuery"):
        raise BridgeError("invalid credit operation")
    root = fromstring(xml)
    if (
        len(root) != 1
        or len(root[0]) != 1
        or root[0][0].tag != operation + "Rs"
        or len(root[0][0]) != 1
        or root[0][0][0].tag != "CreditMemoRet"
    ):
        raise BridgeError("exact saved credit required")
    row = root[0][0][0]
    total = sum(Decimal(line["amount"]) for line in payload["lines"])
    if (
        len(row.findall("Memo")) != 1
        or row.findtext("Memo") != memo(payload)
        or len(row.findall("TotalAmount")) != 1
        or decimal_evidence(row.findtext("TotalAmount")) != total
        or len(row.findall("CreditRemaining")) != 1
        or decimal_evidence(row.findtext("CreditRemaining")) != total
    ):
        raise BridgeError("credit source, total or unused amount differs")
    # Reuse exact line/tax/master validation through an internal schema adapter.
    # The original CreditRemaining above is authoritative; no invoice was queried here.
    root[0][0].tag = operation.replace("CreditMemo", "Invoice") + "Rs"
    row.tag = "InvoiceRet"
    row.find("CreditRemaining").tag = "BalanceRemaining"
    for name, value in (("AppliedAmount", "0"), ("IsPaid", "false"), ("IsFinanceCharge", "false")):
        if row.find(name) is not None:
            raise BridgeError("unexpected invoice field in credit response")
        ET.SubElement(row, name).text = value
    for line in list(row):
        line.tag = line.tag.replace("CreditMemoLine", "InvoiceLine")
    result = invoice_receipt(
        ET.tostring(root),
        policy,
        invoice_payload(payload),
        run,
        operation=operation.replace("CreditMemo", "Invoice"),
        txn_id=txn_id,
    )
    result.pop("balance_remaining")
    return {
        **result,
        "credit_remaining": str(total),
        "source_invoice": payload["invoice_txn_id"],
        "verification": "matched-saved-unapplied-credit",
    }


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
    return discovery, {**receipt, "balances": balances}


def verify_balance_effect(payload, before, after):
    if (
        not isinstance(before, dict)
        or before.get("source_invoice") != payload["invoice_txn_id"]
        or after.get("source_invoice") != payload["invoice_txn_id"]
    ):
        raise BridgeError("original credit customer baseline required")
    total = sum(Decimal(line["amount"]) for line in payload["lines"])
    if decimal_evidence(before.get("customer_balance")) - total != decimal_evidence(
        after.get("customer_balance")
    ):
        raise BridgeError("credit customer balance effect differs; never resend")
    return {
        "before": before["customer_balance"],
        "credit": str(total),
        "after": after["customer_balance"],
    }
