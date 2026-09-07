"""Durable exact master observations used by reviewed create/update proposals."""

import json
import time
from dataclasses import asdict
from xml.etree import ElementTree as ET

from qbwc_kit._xml import fromstring

from .config import BridgeError, company_policy_context, strict_keys
from .direct_sdk import company_lock
from .master_records import ACCOUNTS, FIELDS, KINDS, record, target_reference, validate, xml
from .qbwc import DurableQBWCDiscoveryService
from .service import audited
from .validation import canonical, digest


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS master_checks (
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, connector TEXT NOT NULL,
        created_at REAL NOT NULL, specification TEXT NOT NULL, context_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('dispatched','verified','failed')),
        response TEXT, result TEXT)""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS master_check_immutable BEFORE UPDATE ON master_checks
        WHEN OLD.state!='dispatched' OR NEW.state NOT IN ('verified','failed')
        OR OLD.id IS NOT NEW.id OR OLD.actor IS NOT NEW.actor OR OLD.connector IS NOT NEW.connector
        OR OLD.created_at IS NOT NEW.created_at OR OLD.specification IS NOT NEW.specification
        OR OLD.context_hash IS NOT NEW.context_hash
        BEGIN SELECT RAISE(ABORT,'immutable master observation'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS master_check_no_delete BEFORE DELETE ON master_checks
        BEGIN SELECT RAISE(ABORT,'durable master observation'); END""")
    db.execute("""CREATE TABLE IF NOT EXISTS master_evidence_links (
        sequence INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), evidence TEXT NOT NULL)""")
    for action in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS master_evidence_no_{action.lower()}
            BEFORE {action} ON master_evidence_links BEGIN SELECT RAISE(ABORT,'immutable master evidence'); END""")


def context(policy, connector, spec):
    return digest(
        {
            "policy": company_policy_context(policy),
            "connector": asdict(connector),
            "specification": spec,
        }
    )


def request(policy, spec, run):
    root = fromstring(DurableQBWCDiscoveryService._discovery_request(run, "17.0"))
    batch = root[0]
    pref = ET.SubElement(batch, "PreferencesQueryRq", requestID=run + "3")
    for field in ("MultiCurrencyPreferences", "SalesTaxPreferences"):
        ET.SubElement(pref, "IncludeRetElement").text = field
    selectors = []
    if spec.get("list_id") or spec.get("full_name"):
        selectors.append(
            (
                KINDS[spec["kind"]],
                "ListID" if spec.get("list_id") else "FullName",
                spec.get("list_id") or spec["full_name"],
                FIELDS[spec["kind"]],
            )
        )
    payload = spec.get("payload")
    if payload:
        fields = payload["fields"]
        if "name" in fields:
            selectors.append(
                (
                    "Entity" if spec["kind"] in ("customer", "supplier") else "Item",
                    "FullName",
                    fields["name"],
                    ("ListID", "Name", "FullName"),
                )
            )
        for name in ACCOUNTS:
            if name in fields:
                selectors.append(
                    (
                        "Account",
                        "ListID",
                        policy.account_roles[fields[name]],
                        ("ListID", "IsActive", "AccountType", "CurrencyRef"),
                    )
                )
    for index, (kind, selector, value, fields) in enumerate(selectors, 4):
        node = ET.SubElement(batch, kind + "QueryRq", requestID=run + str(index))
        ET.SubElement(node, selector).text = value
        # Enterprise 24 omits item ExternalGUID when IncludeRetElement is used,
        # even when explicitly requested. Read the one exact item in full and
        # retain only the reviewed projection below. Never broaden its selector.
        if kind not in ("ItemService", "ItemInventory"):
            for field in fields:
                ET.SubElement(node, "IncludeRetElement").text = field
    return xml(root)


def response(text, policy, connector, spec, run):
    root = fromstring(text)
    queries = list(fromstring(request(policy, spec, run))[0])
    if (
        root.tag != "QBXML"
        or len(root) != 1
        or root[0].tag != "QBXMLMsgsRs"
        or len(root[0]) != len(queries)
    ):
        raise BridgeError("incomplete master observation response")
    discovery = ET.Element("QBXML")
    base = ET.SubElement(discovery, "QBXMLMsgsRs")
    for child in list(root[0])[:2]:
        base.append(child)
    DurableQBWCDiscoveryService._verify_discovery_response(
        xml(discovery), {"correlation": run, "country": "US", "qbxml_version": "17.0"}, connector
    )
    values = []
    for query, rs in zip(queries[2:], list(root[0])[2:], strict=True):
        if (
            rs.tag != query.tag[:-2] + "Rs"
            or rs.get("requestID") != query.get("requestID")
            or rs.get("iteratorRemainingCount") not in (None, "0")
        ):
            raise BridgeError("uncorrelated master observation")
        if len(rs) > 1:
            raise BridgeError("ambiguous master observation")
        if not len(rs):
            if query.tag not in ("EntityQueryRq", "ItemQueryRq") or (
                rs.get("statusCode"),
                rs.get("statusSeverity"),
            ) not in (("1", "Info"), ("500", "Warn")):
                raise BridgeError("required master observation missing")
            values.append(None)
            continue
        if rs.get("statusCode") != "0" or rs.get("statusSeverity") != "Info":
            raise BridgeError("unsuccessful master observation")
        kind = query.tag.removesuffix("QueryRq")
        if kind not in ("Entity", "Item") and rs[0].tag != kind + "Ret":
            raise BridgeError("unexpected master record type")
        projection = {f.text for f in query.findall("IncludeRetElement")}
        if kind in ("ItemService", "ItemInventory"):
            projection = FIELDS["service" if kind == "ItemService" else "inventory"]
        value = record(rs[0], projection)
        if query.findtext("ListID") and value.get("ListID") != query.findtext("ListID"):
            raise BridgeError("master ListID differs")
        if (
            query.findtext("FullName")
            and str(value.get("FullName", value.get("Name", ""))).casefold()
            != query.findtext("FullName").casefold()
        ):
            raise BridgeError("master name differs")
        values.append(value)
    prefs = values.pop(0)
    if prefs.get("MultiCurrencyPreferences", {}).get("IsMultiCurrencyOn") != "false":
        raise BridgeError("master qualification currently requires single currency")
    original = values.pop(0) if spec.get("list_id") or spec.get("full_name") else None
    if original is not None:
        target_reference(original)
        if original.get("ParentRef") or original.get("UnitOfMeasureSetRef"):
            raise BridgeError("hierarchical or unit-of-measure master edits unsupported")
    payload = spec.get("payload")
    if payload:
        if payload["action"] == "update":
            if target_reference(original) != payload["target"]:
                raise BridgeError("stale master: re-read and review its edit sequence")
            if (
                payload["kind"] == "service"
                and (
                    "SalesAndPurchase"
                    if payload["service_mode"] == "sales-purchase"
                    else "SalesOrPurchase"
                )
                not in original
            ):
                raise BridgeError("service aggregate conversion is unsupported")
        fields = payload["fields"]
        if "name" in fields:
            collision = values.pop(0)
            if collision and (original is None or collision.get("ListID") != original["ListID"]):
                raise BridgeError("master name already exists; select the existing record")
        for name in ACCOUNTS:
            if name in fields:
                account = values.pop(0)
                if (
                    account.get("IsActive") != "true"
                    or account.get("AccountType") not in ACCOUNTS[name][1]
                ):
                    raise BridgeError("master account role is inactive or incompatible")
                if account.get("CurrencyRef"):
                    raise BridgeError("foreign-currency master account unsupported")
    return {
        "record": original,
        "target": target_reference(original) if original else None,
        "payload_sha256": digest(payload) if payload else None,
    }


def exchange(request_text, folder, callback):
    from .sample_posting import windows_exchange

    return windows_exchange(request_text, None, folder, callback, helper="native_master.ps1")


@audited
def read(
    bridge,
    token,
    company,
    connector_id,
    kind,
    *,
    list_id=None,
    full_name=None,
    payload=None,
    run_id=None,
    recover=False,
    transport=exchange,
):
    config, actor, policy, store = bridge._context(token, company, "validate")
    config.authorize(actor, company, "read")
    connector = config.connectors.get(connector_id)
    if connector is None or connector.company != company or connector.identity_sha256 == "0" * 64:
        raise BridgeError("confirmed company connector required")
    if not isinstance(kind, str) or kind not in KINDS:
        raise BridgeError("unsupported master kind")
    if payload:
        payload = validate(payload, policy)
        if payload["kind"] != kind:
            raise BridgeError("master kind mismatch")
        list_id = payload.get("target", {}).get("list_id")
    elif not list_id and not full_name:
        raise BridgeError("exact existing ListID or proposed fields required")
    if full_name is not None and (
        payload is not None
        or list_id is not None
        or not isinstance(full_name, str)
        or not 1 <= len(full_name) <= 41
        or any(ord(c) < 32 for c in full_name)
    ):
        raise BridgeError("one exact bounded master selector required")
    if list_id:
        from .account_lookup import validate_list_id

        validate_list_id(list_id)
    spec = {"kind": kind, "list_id": list_id, "payload": payload}
    if full_name is not None:
        spec["full_name"] = full_name
    run = run_id or str(time.time_ns())[-15:]
    if not isinstance(run, str) or not run.isdigit() or not 1 <= len(run) <= 15:
        raise BridgeError("bounded numeric master observation ID required")
    folder = store.path.parent / ("master-read-" + run)
    with company_lock(store.path.with_suffix(".sdk.lock")):
        with store.transaction() as db:
            schema(db)
            if not store.verify_audit(db):
                raise BridgeError("invalid audit")
            row = db.execute("SELECT * FROM master_checks WHERE id=?", (run,)).fetchone()
            if row:
                if (
                    row["actor"] != actor
                    or row["connector"] != connector_id
                    or row["context_hash"] != context(policy, connector, spec)
                ):
                    raise BridgeError("master observation ownership or context changed")
                if row["state"] == "verified":
                    return json.loads(row["result"])
                if not recover or row["state"] != "dispatched":
                    raise BridgeError("master read recovery required")
            else:
                if recover:
                    raise BridgeError("original master read required")
                db.execute(
                    "INSERT INTO master_checks VALUES (?,?,?,?,?,?,'dispatched',NULL,NULL)",
                    (
                        run,
                        actor,
                        connector_id,
                        bridge.clock(),
                        canonical(spec),
                        context(policy, connector, spec),
                    ),
                )
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    None,
                    "master_read_dispatched",
                    {"run": run, "specification_hash": digest(spec)},
                )
        try:
            if not recover:
                transport(request(policy, spec, run), folder, lambda text: False)
            if not (folder / "closed.txt").exists():
                raise BridgeError("native master read has not closed")
            text = (folder / "preflight.response.xml").read_text(encoding="utf-8")
            result = response(text, policy, connector, spec, run)
            current, current_actor, current_policy, _ = bridge._context(token, company, "validate")
            current.authorize(current_actor, company, "read")
            if (
                current_actor != actor
                or current.connectors.get(connector_id) != connector
                or context(current_policy, connector, spec) != context(policy, connector, spec)
            ):
                raise BridgeError("master observation authority changed")
            result.update(
                reference={"transport": "master-sdk", "connector": connector_id, "id": run},
                context_sha256=context(policy, connector, spec),
                response_sha256=digest(text),
            )
            with store.transaction() as db:
                db.execute(
                    "UPDATE master_checks SET state='verified',response=?,result=? WHERE id=?",
                    (text, canonical(result), run),
                )
                store.event(
                    db,
                    bridge.clock(),
                    actor,
                    None,
                    "master_read_verified",
                    {"run": run, "response_sha256": digest(text)},
                )
            return result
        except (BridgeError, OSError):
            # A surviving helper can still be recovered using its original run ID.
            if (folder / "closed.txt").exists():
                with store.transaction() as db:
                    db.execute(
                        "UPDATE master_checks SET state='failed' WHERE id=? AND state='dispatched'",
                        (run,),
                    )
            raise


def resolve(config, policy, store, db, actor, payload, reference, now):
    strict_keys(reference, {"transport", "connector", "id"})
    if reference["transport"] != "master-sdk":
        raise BridgeError("verified master observation required")
    row = db.execute("SELECT * FROM master_checks WHERE id=?", (reference["id"],)).fetchone()
    connector = config.connectors.get(reference["connector"])
    if (
        row is None
        or connector is None
        or connector.company != policy.id
        or row["state"] != "verified"
        or row["actor"] != actor
        or row["connector"] != connector.id
    ):
        raise BridgeError("owned verified master observation required")
    spec = {
        "kind": payload["kind"],
        "list_id": payload.get("target", {}).get("list_id"),
        "payload": payload,
    }
    if (
        row["context_hash"] != context(policy, connector, spec)
        or not 0 <= now - row["created_at"] < policy.invoice_evidence_max_age_seconds
        or not store.verify_audit(db)
    ):
        raise BridgeError("master observation is stale or context changed")
    for permission in ("read", "validate"):
        config.authorize(actor, policy.id, permission)
    result = response(row["response"], policy, connector, spec, row["id"])
    return {
        "reference": reference,
        "context_sha256": row["context_hash"],
        "observed_at": row["created_at"],
        "original": result["record"],
        "response_sha256": digest(row["response"]),
    }


def require(config, policy, store, db, job, now):
    evidence = job.get("master_evidence")
    if (
        evidence is None
        or resolve(
            config, policy, store, db, job["submitter"], job["payload"], evidence["reference"], now
        )
        != evidence
    ):
        raise BridgeError("fresh exact master evidence required")
