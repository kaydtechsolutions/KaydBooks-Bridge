"""Bounded sample inventory_transfers through the shared QBWC lifecycle only."""

import math
from dataclasses import asdict
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .inventory_transfers import (
    add_request as add_request,
)  # noqa: F401
from .inventory_transfers import (
    append_check,
    append_query,
    plan,
    validate_check,
    validate_receipt,
)
from .qbwc import DurableQBWCDiscoveryService
from .service import Bridge
from .validation import digest, validate_source


def context_hash(policy, job, connector):
    return digest(
        {
            "policy": plan(policy, job["payload"])["context_sha256"],
            "gate": policy.sample_inventory_transfer_posting,
            "connector": asdict(connector),
        }
    )


def preflight(policy, payload, run):
    return append_query(
        append_check(
            DurableQBWCDiscoveryService._discovery_request(run, "17.0"), run, plan(policy, payload)
        ),
        run + "999",
        ref_number=payload["ref_number"],
    )


def check_preflight(response, policy, payload, connector, run, *, recovering=False):
    root = fromstring(response)
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or not len(root[0]):
        raise BridgeError("invalid inventory_transfer preflight envelope")
    collision = root[0][-1]
    if collision.tag != "TransferInventoryQueryRs" or collision.get("requestID") != run + "999":
        raise BridgeError("uncorrelated inventory_transfer duplicate query")
    root[0].remove(collision)
    discovery, balances = validate_check(
        ET.tostring(root), run, plan(policy, payload), recovering=recovering or len(collision) > 0
    )
    DurableQBWCDiscoveryService._verify_discovery_response(
        discovery, {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    if len(collision) == 0 and (collision.get("statusCode"), collision.get("statusSeverity")) in (
        ("1", "Info"),
        ("500", "Warn"),
    ):
        return None, balances
    isolated = ET.Element("QBXML")
    ET.SubElement(isolated, "QBXMLMsgsRs").append(collision)
    return validate_receipt(ET.tostring(isolated), policy, payload, run + "999"), balances


def gate(config, actor, policy, job, now):
    if job["operation"] != "inventory-transfer.create":
        raise BridgeError("inventory_transfer operation required")
    for permission in ("post-sample", "read", "validate"):
        config.authorize(actor, policy.id, permission)
    config.authorize(job["submitter"], policy.id, "submit")
    settings = policy.sample_inventory_transfer_posting
    if (
        not settings
        or not math.isfinite(now)
        or not math.isfinite(settings["expires_at"])
        or now >= settings["expires_at"]
        or not job["payload"]["ref_number"].startswith(settings["ref_prefix"])
    ):
        raise BridgeError("controlled sample authorization is absent, expired or outside scope")
    connector = config.connectors.get(settings["connector"])
    if connector is None or connector.company != policy.id or connector.identity_sha256 == "0" * 64:
        raise BridgeError("confirmed sample connector required")
    if job["submitter"] != actor:
        raise BridgeError("sample posting requires job ownership")
    validate_source(job["source"], policy)
    Bridge._approval(config, policy, job)
    return connector
