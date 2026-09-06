"""Fixed BillPaymentCheck requests and exact saved allocation verification."""

from decimal import Decimal
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError
from .invoice_commercial import decimal_evidence
from .invoice_compatibility import required_id
from .invoice_receipt import _request_id
from .supplier_payments import append_check, plan
from .validation import digest

FIELDS = (
    "TxnID",
    "EditSequence",
    "PayeeEntityRef",
    "APAccountRef",
    "TxnDate",
    "RefNumber",
    "Amount",
    "AmountInHomeCurrency",
    "IsToBePrinted",
    "CurrencyRef",
    "ExchangeRate",
    "BankAccountRef",
    "AppliedToTxnRet",
)


def add_request(policy, payload, run):
    check = plan(policy, payload)
    root = ET.Element("QBXML")
    batch = ET.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
    rq = ET.SubElement(batch, "BillPaymentCheckAddRq", requestID=_request_id(run))
    add = ET.SubElement(rq, "BillPaymentCheckAdd")
    for field, key in (("PayeeEntityRef", "vendor"), ("APAccountRef", "payable")):
        ET.SubElement(ET.SubElement(add, field), "ListID").text = check["binding"][key]
    ET.SubElement(add, "TxnDate").text = payload["txn_date"]
    ET.SubElement(ET.SubElement(add, "BankAccountRef"), "ListID").text = check["binding"]["bank"]
    ET.SubElement(add, "RefNumber").text = payload["ref_number"]
    for allocation in payload["allocations"]:
        node = ET.SubElement(add, "AppliedToTxnAdd")
        ET.SubElement(node, "TxnID").text = allocation["txn_id"]
        ET.SubElement(node, "PaymentAmount").text = allocation["amount"]
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def append_query(discovery, run, *, txn_id=None, ref_number=None):
    _request_id(run)
    if (txn_id is None) == (ref_number is None):
        raise BridgeError("select exactly one payment identity")
    root = fromstring(discovery)
    query = ET.SubElement(root[0], "BillPaymentCheckQueryRq", requestID=run)
    ET.SubElement(query, "TxnID" if txn_id else "RefNumber").text = (
        required_id(txn_id) if txn_id else ref_number
    )
    ET.SubElement(query, "IncludeLineItems").text = "true"
    for field in FIELDS:
        ET.SubElement(query, "IncludeRetElement").text = field
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def lookup_context(policy, payload, txn_id):
    return digest(
        {
            "schema": "supplier-payment-receipt-v1",
            "plan": plan(policy, payload)["context_sha256"],
            "txn_id": required_id(txn_id),
        }
    )


def append_lookup(discovery, run, policy, payload, txn_id):
    return append_query(
        append_check(discovery, run, plan(policy, payload)), run + "99", txn_id=txn_id
    )


def validate_receipt(xml, policy, payload, run, *, operation="BillPaymentCheckQuery", txn_id=None):
    check = plan(policy, payload)
    responses = list(parse_response(xml))
    if len(responses) != 1:
        raise BridgeError("exactly one payment receipt response required")
    rs = responses[0]
    if (
        operation not in ("BillPaymentCheckQuery", "BillPaymentCheckAdd")
        or rs.request_id != run
        or rs.entity != "BillPaymentCheck"
        or rs.status_code != 0
        or rs.status_severity != "Info"
        or len(rs.records) != 1
        or fromstring(xml)[0][0].tag != operation + "Rs"
    ):
        raise BridgeError("payment receipt status, correlation or cardinality differs")
    row = rs.records[0]
    saved_id = required_id(row.get("TxnID"))
    if txn_id is not None and saved_id != txn_id:
        raise BridgeError("payment TxnID differs")
    for field, key in (
        ("PayeeEntityRef", "vendor"),
        ("APAccountRef", "payable"),
        ("BankAccountRef", "bank"),
    ):
        if (
            not isinstance(row.get(field), dict)
            or row[field].get("ListID") != check["binding"][key]
        ):
            raise BridgeError("saved payment master differs")
    if row.get("TxnDate") != payload["txn_date"] or row.get("RefNumber") != payload["ref_number"]:
        raise BridgeError("saved payment date or reference differs")
    import re

    if not isinstance(row.get("EditSequence"), str) or not re.fullmatch(
        r"[0-9]{1,16}", row["EditSequence"]
    ):
        raise BridgeError("saved payment edit sequence invalid")
    if (
        "CurrencyRef" in row
        or "CreditCardTxnInfo" in row
        or decimal_evidence(row.get("ExchangeRate", "1")) != 1
    ):
        raise BridgeError("unsupported payment currency or card processing")
    total = Decimal(payload["total_amount"])
    expected = {a["txn_id"]: Decimal(a["amount"]) for a in payload["allocations"]}
    if (
        decimal_evidence(row.get("Amount")) != total
        or decimal_evidence(row.get("AmountInHomeCurrency", str(total))) != total
        or row.get("IsToBePrinted") != "false"
    ):
        raise BridgeError("saved supplier payment amount or print state differs")
    allocations = row.get("AppliedToTxnRet", [])
    if isinstance(allocations, dict):
        allocations = [allocations]
    if not isinstance(allocations, list) or len(allocations) != len(expected):
        raise BridgeError("saved payment allocations differ")
    seen = set()
    related = {}
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise BridgeError("malformed saved allocation")
        key = allocation.get("TxnID")
        if (
            not isinstance(key, str)
            or key not in expected
            or key in seen
            or allocation.get("TxnType") != "Bill"
            or decimal_evidence(allocation.get("Amount")) != expected[key]
            or decimal_evidence(allocation.get("DiscountAmount", "0")) != 0
            or any(
                k in allocation for k in ("DiscountAccountRef", "DiscountClassRef", "TxnLineDetail")
            )
        ):
            raise BridgeError("saved payment application differs")
        seen.add(key)
        # LinkedTxn describes other transactions associated with the bill;
        # only this AppliedToTxnRet.Amount is the current payment allocation.
        links = allocation.get("LinkedTxn", [])
        if isinstance(links, dict):
            links = [links]
        if not isinstance(links, list) or len(links) > 1000:
            raise BridgeError("invalid related bill history")
        observed, link_ids = [], set()
        for link in links:
            if not isinstance(link, dict):
                raise BridgeError("malformed related bill history")
            link_id = required_id(link.get("TxnID"))
            if (
                link_id in link_ids
                or link.get("TxnType") not in ("BillPaymentCheck", "VendorCredit")
                or link.get("LinkType") != "AMTTYPE"
            ):
                raise BridgeError("unsupported or duplicated related bill history")
            link_ids.add(link_id)
            observed.append(
                {
                    "txn_id": link_id,
                    "txn_type": link["TxnType"],
                    "amount": str(decimal_evidence(link.get("Amount"))),
                }
            )
        if observed:
            related[key] = observed
    return {
        "txn_id": saved_id,
        "edit_sequence": row["EditSequence"],
        "ref_number": payload["ref_number"],
        "total_amount": str(total),
        "allocations": {key: str(value) for key, value in expected.items()},
        "related_bill_transactions_observed": related,
        "verification": "matched-saved-supplier-payment",
    }


def validate_lookup(xml, run, policy, payload, txn_id):
    from .supplier_payments import validate_check

    root = fromstring(xml)
    if len(root) != 1 or not len(root[0]):
        raise BridgeError("invalid payment receipt envelope")
    payment = root[0][-1]
    root[0].remove(payment)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=True
    )
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(payment)
    receipt = validate_receipt(ET.tostring(isolated), policy, payload, run + "99", txn_id=txn_id)
    return discovery, {**receipt, "bill_balances": balances}


def verify_balance_effect(payload, before, after):
    keys = {a["txn_id"] for a in payload["allocations"]}
    if not isinstance(before, dict) or set(before) != keys or set(after) != keys:
        raise BridgeError("payment requires original bill balance evidence")
    effects = {}
    for allocation in payload["allocations"]:
        key, amount = allocation["txn_id"], Decimal(allocation["amount"])
        prior, current = before[key], after[key]
        if (
            any(prior[k] != current[k] for k in ("txn_id", "txn_date", "ref_number", "total"))
            or Decimal(current["balance"]) != Decimal(prior["balance"]) - amount
            or Decimal(current["applied"]) != Decimal(prior["applied"]) + amount
        ):
            raise BridgeError("payment bill balance effect differs; never resend")
        effects[key] = {
            "before": prior["balance"],
            "payment": str(amount),
            "after": current["balance"],
        }
    return effects
