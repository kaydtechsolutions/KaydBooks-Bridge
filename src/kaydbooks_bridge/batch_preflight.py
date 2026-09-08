"""Bounded, company-bound readback for exact transaction references; never writes."""

import re
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, company_policy_context, strict_keys
from .reference_data import _value
from .validation import digest

OPERATION = "batch-preflight.read"
ENTITIES = ("Invoice", "SalesReceipt", "ReceivePayment", "Check", "JournalEntry")


def plan(policy, specification):
    strict_keys(specification, {"transactions"})
    transactions = specification["transactions"]
    if not isinstance(transactions, list) or not 1 <= len(transactions) <= 30:
        raise BridgeError("preflight requires 1-30 exact transaction references")
    seen = set()
    for value in transactions:
        strict_keys(value, {"entity", "ref_number"})
        if value["entity"] not in ENTITIES or not isinstance(value["ref_number"], str):
            raise BridgeError("unsupported preflight selector")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,11}", value["ref_number"]):
            raise BridgeError("invalid exact transaction reference")
        key = (value["entity"], value["ref_number"])
        if key in seen:
            raise BridgeError("duplicate preflight selector")
        seen.add(key)
    return {
        "operation": OPERATION,
        "specification": specification,
        "context_sha256": digest(
            {
                "schema": OPERATION,
                "policy": company_policy_context(policy),
                "specification": specification,
            }
        ),
    }


def append_queries(request, run, check):
    root = fromstring(request)
    for index, selector in enumerate(check["specification"]["transactions"], 3):
        query = ET.SubElement(root[0], selector["entity"] + "QueryRq", requestID=run + str(index))
        ET.SubElement(query, "RefNumber").text = selector["ref_number"]
        ET.SubElement(query, "IncludeLineItems").text = "true"
        if selector["entity"] == "Invoice":
            ET.SubElement(query, "IncludeLinkedTxns").text = "true"
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_response(response, run, check):
    if len(response) > 16 * 1024 * 1024:
        raise BridgeError("preflight response exceeds size limit")
    root = fromstring(response)
    selectors = check["specification"]["transactions"]
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != 2 + len(selectors)
    ):
        raise BridgeError("complete preflight response required")
    results = []
    for index, selector in enumerate(selectors, 3):
        answer = root[0][index - 1]
        if answer.tag != selector["entity"] + "QueryRs" or answer.get("requestID") != run + str(
            index
        ):
            raise BridgeError("preflight response correlation mismatch")
        status = (answer.get("statusCode"), answer.get("statusSeverity"))
        if status not in (("0", "Info"), ("1", "Info"), ("500", "Warn")):
            raise BridgeError(
                "QuickBooks exact-reference query failed: " + str(answer.get("statusCode"))
            )
        records = []
        seen = set()
        for node in answer:
            if node.tag != selector["entity"] + "Ret" or status != ("0", "Info"):
                raise BridgeError("unexpected preflight record")
            value = _value(node)
            if (
                not value.get("TxnID")
                or value["TxnID"] in seen
                or value.get("RefNumber") != selector["ref_number"]
            ):
                raise BridgeError("ambiguous or mismatched preflight record")
            seen.add(value["TxnID"])
            records.append(value)
        results.append({**selector, "records": records})
    for node in list(root[0])[2:]:
        root[0].remove(node)
    return ET.tostring(root, encoding="unicode"), {
        "transactions": results,
        "complete": True,
        "read_only": True,
        "response_sha256": digest(response),
    }
