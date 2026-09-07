"""Recorded credit-card refunds only; never invoke a payment processor."""

from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError
from .customer_payments import validate_payload as payment_payload
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .invoice_receipt import _request_id
from .validation import digest

FIELDS = {
    "Preferences": ("MultiCurrencyPreferences",),
    "Customer": ("ListID", "IsActive", "Balance", "CurrencyRef"),
    "Account": ("ListID", "IsActive", "AccountType", "Balance", "CurrencyRef"),
    "PaymentMethod": ("ListID", "IsActive", "PaymentMethodType"),
    "CreditMemo": (
        "TxnID",
        "EditSequence",
        "CustomerRef",
        "ARAccountRef",
        "TxnDate",
        "IsPending",
        "TotalAmount",
        "SalesTaxTotal",
        "CreditRemaining",
        "CurrencyRef",
        "ExchangeRate",
    ),
}
RECEIPT_FIELDS = (
    "TxnID",
    "EditSequence",
    "CustomerRef",
    "RefundFromAccountRef",
    "ARAccountRef",
    "TxnDate",
    "RefNumber",
    "TotalAmount",
    "PaymentMethodRef",
    "CurrencyRef",
    "ExchangeRate",
    "RefundAppliedToTxnRet",
)


def validate_payload(payload, policy):
    value = payment_payload(payload, policy)
    if (
        not value["allocations"]
        or sum(Decimal(a["amount"]) for a in value["allocations"]) != Decimal(value["total_amount"])
        or len(value["ref_number"]) > 11
    ):
        raise BridgeError(
            "refund requires exact credit allocations and reference at most 11 characters"
        )
    return value


def plan(policy, payload):
    payload = validate_payload(payload, policy)
    m = policy.payment_masters
    binding = {
        "customer": m["customers"][payload["customer_id"]],
        "receivable": m["receivable"],
        "deposit": m["deposits"][payload["deposit_id"]],
        "method": m["methods"][payload["method_id"]],
    }
    queries = [
        ("Preferences", None),
        ("Customer", binding["customer"]),
        ("Account", binding["receivable"]),
        ("Account", binding["deposit"]),
        ("PaymentMethod", binding["method"]),
    ] + [("CreditMemo", a["txn_id"]) for a in payload["allocations"]]
    return {
        "payload": payload,
        "binding": binding,
        "queries": queries,
        "context_sha256": digest(
            {"schema": "recorded-refund-v1", "payload": payload, "binding": binding}
        ),
    }


def append_check(discovery, run, check):
    _request_id(run)
    root = fromstring(discovery)
    for i, (entity, key) in enumerate(check["queries"], 3):
        q = ET.SubElement(root[0], entity + "QueryRq", requestID=run + str(i))
        if key is not None:
            ET.SubElement(q, "TxnID" if entity == "CreditMemo" else "ListID").text = key
        for field in FIELDS[entity]:
            ET.SubElement(q, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_check(xml, run, check, *, recovering=False):
    responses = list(parse_response(xml))
    if len(responses) != 2 + len(check["queries"]):
        raise BridgeError("refund response set differs")
    rows = []
    for i, (rs, (entity, key)) in enumerate(zip(responses[2:], check["queries"], strict=True), 3):
        if (
            rs.entity != entity
            or rs.request_id != run + str(i)
            or rs.status_code != 0
            or rs.status_severity != "Info"
            or len(rs.records) != 1
        ):
            raise BridgeError("refund evidence must be exact and complete")
        row = rs.records[0]
        if key is not None and row.get("TxnID" if entity == "CreditMemo" else "ListID") != key:
            raise BridgeError("refund master or credit identity differs")
        if "CurrencyRef" in row or (
            entity not in ("Preferences", "CreditMemo") and row.get("IsActive") != "true"
        ):
            raise BridgeError("active single-currency refund masters required")
        rows.append(row)
    prefs = rows[0].get("MultiCurrencyPreferences")
    if not isinstance(prefs, dict) or prefs.get("IsMultiCurrencyOn") != "false":
        raise BridgeError("single-currency refund required")
    if (
        rows[2].get("AccountType") != "AccountsReceivable"
        or rows[3].get("AccountType") != "Bank"
        or rows[4].get("PaymentMethodType")
        not in {"AmericanExpress", "Discover", "MasterCard", "OtherCreditCard", "Visa"}
    ):
        raise BridgeError("refund requires AR, Bank and a credit-card payment method")
    balances = {
        "customer": str(decimal_evidence(rows[1].get("Balance"))),
        "bank": str(decimal_evidence(rows[3].get("Balance"))),
        "credits": {},
    }
    for row, allocation in zip(rows[5:], check["payload"]["allocations"], strict=True):
        for field, key in (("CustomerRef", "customer"), ("ARAccountRef", "receivable")):
            if (
                not isinstance(row.get(field), dict)
                or row[field].get("ListID") != check["binding"][key]
            ):
                raise BridgeError("refund credit customer or AR differs")
        required_id(row.get("EditSequence"))
        from datetime import date

        try:
            if (
                date.fromisoformat(row["TxnDate"]).isoformat() != row["TxnDate"]
                or row["TxnDate"] > check["payload"]["txn_date"]
            ):
                raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise BridgeError("refund precedes credit or credit date invalid") from exc
        available = decimal_evidence(row.get("CreditRemaining"))
        if (
            row.get("IsPending") != "false"
            or decimal_evidence(row.get("SalesTaxTotal")) != 0
            or decimal_evidence(row.get("ExchangeRate", "1")) != 1
            or not 0 <= available <= decimal_evidence(row.get("TotalAmount"))
        ):
            raise BridgeError("unsupported refund credit")
        if not recovering and available < Decimal(allocation["amount"]):
            raise BridgeError("refund exceeds unused credit")
        balances["credits"][allocation["txn_id"]] = str(available)
    root = fromstring(xml)
    for n in list(root[0])[2:]:
        root[0].remove(n)
    return ET.tostring(root, encoding="unicode"), balances


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    add = ET.SubElement(
        ET.SubElement(batch, "ARRefundCreditCardAddRq", requestID=_request_id(run)),
        "ARRefundCreditCardAdd",
    )
    for field, key in (
        ("CustomerRef", "customer"),
        ("RefundFromAccountRef", "deposit"),
        ("ARAccountRef", "receivable"),
    ):
        ET.SubElement(ET.SubElement(add, field), "ListID").text = check["binding"][key]
    ET.SubElement(add, "TxnDate").text = payload["txn_date"]
    ET.SubElement(add, "RefNumber").text = payload["ref_number"]
    ET.SubElement(ET.SubElement(add, "PaymentMethodRef"), "ListID").text = check["binding"][
        "method"
    ]
    for allocation in payload["allocations"]:
        node = ET.SubElement(add, "RefundAppliedToTxnAdd")
        ET.SubElement(node, "TxnID").text = allocation["txn_id"]
        ET.SubElement(node, "RefundAmount").text = allocation["amount"]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("exact refund identity required")
    root = fromstring(discovery)
    q = ET.SubElement(root[0], "ARRefundCreditCardQueryRq", requestID=_request_id(run))
    ET.SubElement(q, "TxnID" if txn_id else "RefNumber").text = (
        required_id(txn_id) if txn_id else ref_number
    )
    ET.SubElement(q, "IncludeLineItems").text = "true"
    for field in RECEIPT_FIELDS:
        ET.SubElement(q, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def lookup_context(policy, payload, txn_id):
    return digest(
        {
            "schema": "refund-outcome-v1",
            "context": plan(policy, payload)["context_sha256"],
            "txn_id": required_id(txn_id),
        }
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_receipt(
    xml, policy, payload, run, *, operation="ARRefundCreditCardQuery", txn_id=None
):
    check = plan(policy, payload)
    root = fromstring(xml)
    responses = list(parse_response(xml))
    if (
        operation not in ("ARRefundCreditCardAdd", "ARRefundCreditCardQuery")
        or len(responses) != 1
        or root[0][0].tag != operation + "Rs"
    ):
        raise BridgeError("exact refund receipt required")
    rs = responses[0]
    if (
        rs.entity != "ARRefundCreditCard"
        or rs.request_id != run
        or rs.status_code != 0
        or rs.status_severity != "Info"
        or len(rs.records) != 1
    ):
        raise BridgeError("refund receipt status or correlation differs")
    row = rs.records[0]
    saved_id = required_id(row.get("TxnID"))
    required_id(row.get("EditSequence"))
    if txn_id is not None and saved_id != txn_id:
        raise BridgeError("refund transaction identity differs")
    for field, key in (
        ("CustomerRef", "customer"),
        ("ARAccountRef", "receivable"),
        ("RefundFromAccountRef", "deposit"),
        ("PaymentMethodRef", "method"),
    ):
        if (
            not isinstance(row.get(field), dict)
            or row[field].get("ListID") != check["binding"][key]
        ):
            raise BridgeError("refund saved master differs")
    if (
        row.get("TxnDate") != payload["txn_date"]
        or row.get("RefNumber") != payload["ref_number"]
        or decimal_evidence(row.get("TotalAmount")) != Decimal(payload["total_amount"])
        or "CreditCardTxnInfo" in row
        or "CurrencyRef" in row
        or decimal_evidence(row.get("ExchangeRate", "1")) != 1
    ):
        raise BridgeError("refund amount, date, currency or processing differs")
    allocations = row.get("RefundAppliedToTxnRet", [])
    if isinstance(allocations, dict):
        allocations = [allocations]
    expected = {a["txn_id"]: Decimal(a["amount"]) for a in payload["allocations"]}
    if not isinstance(allocations, list) or len(allocations) != len(expected):
        raise BridgeError("refund saved allocations differ")
    seen = set()
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise BridgeError("invalid refund allocation")
        key = required_id(allocation.get("TxnID"))
        if (
            key in seen
            or key not in expected
            or allocation.get("TxnType") != "CreditMemo"
            or decimal_evidence(allocation.get("RefundAmount")) != expected[key]
            or (
                (operation == "ARRefundCreditCardAdd" or "CreditRemaining" in allocation)
                and decimal_evidence(allocation.get("CreditRemaining")) < 0
            )
        ):
            raise BridgeError("refund saved credit allocation differs")
        seen.add(key)
    return {
        "txn_id": saved_id,
        "ref_number": payload["ref_number"],
        "amount": payload["total_amount"],
        "verification": "matched-recorded-refund",
        "payment_processor_invoked": False,
    }


def validate_lookup(xml, run, policy, payload, txn_id):
    root = fromstring(xml)
    refund = root[0][-1]
    root[0].remove(refund)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=True
    )
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(refund)
    receipt = validate_receipt(ET.tostring(isolated), policy, payload, run + "99", txn_id=txn_id)
    return discovery, {**receipt, "balances": balances}


def verify_balance_effect(payload, before, after):
    total = Decimal(payload["total_amount"])
    if (
        not isinstance(before, dict)
        or set(before) != {"customer", "bank", "credits"}
        or set(after) != set(before)
    ):
        raise BridgeError("original refund baseline required")
    expected = {a["txn_id"]: Decimal(a["amount"]) for a in payload["allocations"]}
    if (
        set(before["credits"]) != set(expected)
        or set(after["credits"]) != set(expected)
        or decimal_evidence(after["customer"]) != decimal_evidence(before["customer"]) + total
        or decimal_evidence(after["bank"]) != decimal_evidence(before["bank"]) - total
        or any(
            decimal_evidence(after["credits"][key])
            != decimal_evidence(before["credits"][key]) - amount
            for key, amount in expected.items()
        )
    ):
        raise BridgeError("refund credit/customer/bank effect differs; never resend")
    return {"before": before, "after": after, "refunded": str(total)}
