"""Bounded account preview. No arbitrary queries, balances or bank details."""

from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring
from qbwc_kit.qbxml import parse_response

from .config import BridgeError

FIELDS = ("ListID", "FullName", "AccountType", "IsActive")


def append_query(discovery: str, correlation: str) -> str:
    root = fromstring(discovery)
    batch = root.find("QBXMLMsgsRq")
    account = ET.SubElement(batch, "AccountQueryRq", requestID=f"{correlation}3")
    ET.SubElement(account, "MaxReturned").text = "20"
    ET.SubElement(account, "ActiveStatus").text = "ActiveOnly"
    for name in FIELDS:
        ET.SubElement(account, "IncludeRetElement").text = name
    return '<?xml version="1.0"?><?qbxml version="17.0"?>' + ET.tostring(root, encoding="unicode")


def validate_response(payload: str, correlation: str):
    responses = parse_response(payload)
    if len(responses) != 3 or [r.entity for r in responses] != ["Host", "Company", "Account"]:
        raise BridgeError("account preview response set mismatch")
    account = list(responses)[2]
    if account.request_id != f"{correlation}3":
        raise BridgeError("account preview correlation mismatch")
    # No guessed no-match status: accept only explicit SDK success here.
    if account.status_code != 0 or account.status_severity != "Info":
        raise BridgeError("account preview did not return explicit success")
    if len(account.records) > 20:
        raise BridgeError("account preview exceeded limit")
    records = []
    seen = set()
    for row in account.records:
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in FIELDS):
            raise BridgeError("account preview fields incomplete")
        if row["IsActive"] != "true" or row["ListID"] in seen:
            raise BridgeError("inactive or duplicate account returned")
        seen.add(row["ListID"])
        records.append({k: row[k] for k in FIELDS})
    root = fromstring(payload)
    batch = root.find("QBXMLMsgsRs")
    nodes = batch.findall("AccountQueryRs")
    if len(nodes) != 1:
        raise BridgeError("account preview envelope mismatch")
    batch.remove(nodes[0])
    return ET.tostring(root, encoding="unicode"), records
