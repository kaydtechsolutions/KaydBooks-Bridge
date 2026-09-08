"""Opt-in exact reviewed batches over QBWC with durable at-most-once handout.

No automatic retry of writes. Each entry has a preflight, one handout and an
independent TxnID readback. Any error holds the batch and preserves all evidence.
"""

import json
import os
import time
from decimal import Decimal
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring

from .config import BridgeError
from .qbwc_posting import DurableQBWCPostingService
from .reference_data import _value
from .reviewed_requests import KINDS, build, render, verify
from .validation import canonical, digest


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_batches (
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, connector TEXT NOT NULL,
        approved TEXT NOT NULL, approved_hash TEXT NOT NULL, expires REAL NOT NULL,
        state TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, ticket TEXT,
        phase TEXT NOT NULL DEFAULT 'preflight', detail TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_steps (
        batch_id TEXT NOT NULL, position INTEGER NOT NULL, phase TEXT NOT NULL,
        request TEXT NOT NULL, sent_at REAL NOT NULL,
        PRIMARY KEY(batch_id,position,phase))""")
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_answers (
        batch_id TEXT NOT NULL, position INTEGER NOT NULL, phase TEXT NOT NULL,
        response TEXT NOT NULL, response_hash TEXT NOT NULL,
        PRIMARY KEY(batch_id,position,phase))""")
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_results (
        batch_id TEXT NOT NULL, position INTEGER NOT NULL, record_id TEXT NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(batch_id,position))""")
    for table in ("reviewed_steps", "reviewed_answers", "reviewed_results"):
        for action in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable reviewed evidence'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS reviewed_identity_guard
        BEFORE UPDATE ON reviewed_batches WHEN OLD.id IS NOT NEW.id OR OLD.actor IS NOT NEW.actor
        OR OLD.connector IS NOT NEW.connector OR OLD.approved IS NOT NEW.approved
        OR OLD.approved_hash IS NOT NEW.approved_hash OR OLD.expires IS NOT NEW.expires
        BEGIN SELECT RAISE(ABORT,'immutable reviewed authorization'); END""")


def stage(bridge, token, company, approved):
    config, actor, _, store = bridge._context(token, company, "submit")
    for permission in ("prepare", "validate", "approve", "post-sample", "read"):
        config.authorize(actor, company, permission)
    connector = config.connectors[approved["connector"]]
    if connector.company != company or approved["company"] != company:
        raise BridgeError("reviewed batch company mismatch")
    if not approved.get("authorization") or not 0 < approved["expires_at"] - time.time() <= 86400:
        raise BridgeError("bounded explicit authorization required")
    entries = approved["entries"]
    if not 1 <= len(entries) <= 100 or [e["sequence"] for e in entries] != sorted(
        e["sequence"] for e in entries
    ):
        raise BridgeError("reviewed source sequence required")
    for entry in entries:
        if entry["operation"] not in KINDS or entry.get("currency", "USD") != "USD":
            raise BridgeError("unsupported reviewed entry")
        if Decimal(entry.get("tax_amount", "0")) != 0:
            raise BridgeError("reviewed batch must be zero tax")
        if entry["operation"] in ("invoice.create", "sales-receipt.create", "check.create") and sum(
            Decimal(x["amount"]) for x in entry["lines"]
        ) != Decimal(entry["total_amount"]):
            raise BridgeError("reviewed line total mismatch")
        if entry["operation"] in ("invoice.create", "sales-receipt.create"):
            for line in entry["lines"]:
                if (Decimal(line["quantity"]) * Decimal(line["unit_price"])).quantize(
                    Decimal(".01")
                ) != Decimal(line["amount"]):
                    raise BridgeError("reviewed price extension mismatch")
        if entry["operation"] == "customer-payment.create" and sum(
            Decimal(x["payment_amount"]) for x in entry["allocations"]
        ) != Decimal(entry["total_amount"]):
            raise BridgeError("reviewed payment allocation mismatch")
        if entry["operation"] == "journal.create":
            totals = {
                side: sum(Decimal(x["amount"]) for x in entry["lines"] if x["side"] == side)
                for side in ("debit", "credit")
            }
            if totals["debit"] != totals["credit"] or totals["debit"] != Decimal(
                entry["debit_total"]
            ):
                raise BridgeError("reviewed journal imbalance")
        build(entry, approved["maps"], "1")
    with store.transaction() as db:
        schema(db)
        if (
            not store.verify_audit(db)
            or db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('queued','in-flight','unknown','posted-unverified')"
            ).fetchone()
        ):
            raise BridgeError("company audit or pending write prevents reviewed batch")
        if db.execute(
            "SELECT 1 FROM reviewed_batches WHERE state IN ('prepared','active','held')"
        ).fetchone():
            raise BridgeError("existing reviewed batch must be reconciled")
        db.execute(
            "INSERT INTO reviewed_batches(id,actor,connector,approved,approved_hash,expires,state) VALUES(?,?,?,?,?,?,'prepared')",
            (
                approved["batch_id"],
                actor,
                connector.id,
                canonical(approved),
                digest(approved),
                approved["expires_at"],
            ),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "reviewed_batch_authorized",
            {"batch": approved["batch_id"], "hash": digest(approved), "entries": len(entries)},
        )


def resume_saved_readback(bridge, token, company, batch_id):
    """Resume only an acknowledged saved write, solely at independent readback.

    Preserves the original handout and response. No write can be reissued.
    """
    config, actor, _, store = bridge._context(token, company, "recover")
    for permission in ("read", "validate", "submit", "approve", "post-sample"):
        config.authorize(actor, company, permission)
    with store.transaction() as db:
        run = db.execute("SELECT * FROM reviewed_batches WHERE id=?", (batch_id,)).fetchone()
        if not run or run["state"] != "held" or run["phase"] != "write":
            raise BridgeError("held acknowledged write required")
        approved = json.loads(run["approved"])
        if not store.verify_audit(db) or digest(approved) != run["approved_hash"]:
            raise BridgeError("reviewed evidence integrity failed")
        if bridge.clock() >= run["expires"]:
            raise BridgeError("reviewed authorization expired")
        answer = db.execute(
            "SELECT response FROM reviewed_answers WHERE batch_id=? AND position=? AND phase='write'",
            (batch_id, run["position"]),
        ).fetchone()
        sent = db.execute(
            "SELECT request FROM reviewed_steps WHERE batch_id=? AND position=? AND phase='write'",
            (batch_id, run["position"]),
        ).fetchone()
        if (
            not answer
            or not sent
            or db.execute(
                "SELECT 1 FROM reviewed_steps WHERE batch_id=? AND position=? AND phase='readback'",
                (batch_id, run["position"]),
            ).fetchone()
        ):
            raise BridgeError("acknowledgment missing or readback already attempted")
        root = fromstring(answer[0])
        request = fromstring(sent[0])
        if len(root) != 1 or len(root[0]) != 1:
            raise BridgeError("invalid saved acknowledgment")
        rs = root[0][0]
        rq = request[0][0]
        if (
            rs.tag != rq.tag[:-2] + "Rs"
            or rs.get("requestID") != rq.get("requestID")
            or rs.get("statusCode") != "0"
            or len(rs) != 1
        ):
            raise BridgeError("conclusive saved acknowledgment required")
        identity = verify(approved["entries"][run["position"]], approved["maps"], rs[0])
        db.execute(
            "UPDATE reviewed_batches SET state='prepared',phase='readback',ticket=NULL,detail='' WHERE id=?",
            (batch_id,),
        )
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "reviewed_saved_readback_resumed",
            {"batch": batch_id, "position": run["position"], "id": identity, "write_resend": False},
        )


class ReviewedQBWCService(DurableQBWCPostingService):
    def __post_init__(self):
        super().__post_init__()
        for store in self._stores.values():
            with store.transaction() as db:
                schema(db)

    def _do_authenticate(self, call):
        result = super()._do_authenticate(call)
        if not result[0]:
            return result
        store = self._locate(result[0])
        with store.transaction() as db:
            session = db.execute(
                "SELECT * FROM qbwc_sessions WHERE ticket=?", (result[0],)
            ).fetchone()
            run = db.execute(
                "SELECT * FROM reviewed_batches WHERE connector=? AND state IN ('prepared','active')",
                (session["connector"],),
            ).fetchone()
            if run and not db.execute("SELECT paused FROM control").fetchone()[0]:
                if run["ticket"] is not None and run["ticket"] != result[0]:
                    self.hold(
                        db, store, run, "session interrupted; read-only reconciliation required"
                    )
                elif any(
                    db.execute(f"SELECT 1 FROM {table} WHERE ticket=?", (result[0],)).fetchone()
                    for table in ("qbwc_invoice_jobs", "qbwc_account_jobs", "qbwc_invoice_runs")
                ):
                    self.hold(db, store, run, "conflicting read/write session")
                else:
                    db.execute(
                        "UPDATE reviewed_batches SET state='active',ticket=? WHERE id=?",
                        (result[0], run["id"]),
                    )
        return result

    def hold(self, db, store, run, reason):
        db.execute(
            "UPDATE reviewed_batches SET state='held',detail=? WHERE id=?",
            (reason[:1000], run["id"]),
        )
        db.execute("UPDATE control SET paused=1")
        store.event(
            db,
            self.clock(),
            run["actor"],
            None,
            "reviewed_batch_held",
            {
                "batch": run["id"],
                "position": run["position"],
                "phase": run["phase"],
                "reason": reason[:1000],
            },
        )

    def context(self, db, store, run):
        approved = json.loads(run["approved"])
        if digest(approved) != run["approved_hash"] or not store.verify_audit(db):
            raise BridgeError("reviewed authorization or audit changed")
        for permission in ("read", "validate", "submit", "approve", "post-sample"):
            self.config.authorize(run["actor"], store.company, permission)
        if self.clock() >= run["expires"] or db.execute("SELECT paused FROM control").fetchone()[0]:
            raise BridgeError("reviewed authorization expired or company paused")
        connector = self.config.connectors[run["connector"]]
        if (
            connector.company != store.company
            or connector.identity_sha256 != approved["identity_sha256"]
        ):
            raise BridgeError("reviewed connector identity changed")
        return approved, approved["entries"][run["position"]], connector

    def request(self, db, run, approved, entry):
        phase = run["phase"]
        kind = KINDS[entry["operation"]]
        corr = str(700000000 + run["position"])
        if phase == "write":
            return build(entry, approved["maps"], corr + "3")
        root = fromstring(self._discovery_request(corr, "17.0"))
        query = E.SubElement(root[0], kind + "QueryRq", requestID=corr + "3")
        if phase == "readback":
            answer = db.execute(
                "SELECT response FROM reviewed_answers WHERE batch_id=? AND position=? AND phase='write'",
                (run["id"], run["position"]),
            ).fetchone()
            saved = fromstring(answer[0])[0][0][0]
            E.SubElement(query, "ListID" if kind == "Customer" else "TxnID").text = saved.findtext(
                "ListID" if kind == "Customer" else "TxnID"
            )
        else:
            E.SubElement(query, "FullName" if kind == "Customer" else "RefNumber").text = entry.get(
                "name", entry.get("source_reference")
            )
        if kind != "Customer":
            E.SubElement(query, "IncludeLineItems").text = "true"
        if kind == "Invoice":
            E.SubElement(query, "IncludeLinkedTxns").text = "true"
        if phase == "preflight":
            E.SubElement(root[0], "PreferencesQueryRq", requestID=corr + "4")
        if kind == "ReceivePayment":
            for i, line in enumerate(entry["allocations"], 5):
                q = E.SubElement(root[0], "InvoiceQueryRq", requestID=corr + str(i))
                E.SubElement(q, "TxnID").text = approved["maps"]["invoices"][
                    line["invoice_ref_number"]
                ]["TxnID"]
        if phase == "preflight":
            for i, master in enumerate(self.masters(entry, approved["maps"]), 100):
                q = E.SubElement(root[0], master["kind"] + "QueryRq", requestID=corr + str(i))
                E.SubElement(q, "ListID" if master.get("ListID") else "FullName").text = (
                    master.get("ListID") or master["FullName"]
                )
        return render(root)

    @staticmethod
    def masters(entry, maps):
        records = {}
        for group in maps.values():
            if isinstance(group, dict):
                for value in group.values():
                    if isinstance(value, dict) and value.get("ListID"):
                        records[value["ListID"]] = value
        refs = fromstring(build(entry, maps, "1"))[0][0][0]
        result = []
        seen = set()
        for node in refs.iter("ListID"):
            if node.text not in seen:
                if node.text not in records:
                    raise BridgeError("master identity missing from reviewed mapping")
                result.append(records[node.text])
                seen.add(node.text)
        if entry.get("customer_full_name") and maps["customers"][entry["customer_full_name"]].get(
            "authorized_create"
        ):
            result.append({"kind": "Customer", "FullName": entry["customer_full_name"]})
        return result

    def _do_sendRequestXML(self, call):
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        with store.transaction() as db:
            run = db.execute("SELECT * FROM reviewed_batches WHERE ticket=?", (ticket,)).fetchone()
            if run:
                if run["state"] != "active":
                    return ""
                try:
                    approved, entry, connector = self.context(db, store, run)
                    if os.path.normcase(call.get("strCompanyFileName", "")) != os.path.normcase(
                        self.config.connector_company_file(connector)
                    ):
                        raise BridgeError("exact company file callback mismatch")
                    if (
                        self._callback_version(
                            call.get("qbXMLMajorVers"),
                            call.get("qbXMLMinorVers"),
                            call.get("qbXMLCountry"),
                        )
                        != "17.0"
                    ):
                        raise BridgeError("qbXML 17.0 required")
                    session = db.execute(
                        "SELECT * FROM qbwc_sessions WHERE ticket=?", (ticket,)
                    ).fetchone()
                    hcp = call.get("strHCPResponse") or session["hcp_xml"]
                    if not hcp:
                        raise BridgeError("initial company identity callback required")
                    self._verify_hcp(hcp, connector)
                    if session["hcp_xml"] is None:
                        db.execute(
                            "UPDATE qbwc_sessions SET hcp_xml=?,hcp_hash=? WHERE ticket=?",
                            (hcp, digest({"hcp": hcp}), ticket),
                        )
                    old = db.execute(
                        "SELECT * FROM reviewed_steps WHERE batch_id=? AND position=? AND phase=?",
                        (run["id"], run["position"], run["phase"]),
                    ).fetchone()
                    if old:
                        if run["phase"] == "write":
                            raise BridgeError("write already handed out; NEVER resend")
                        return old["request"]
                    if run["phase"] == "write":
                        before = db.execute(
                            "SELECT sent_at FROM reviewed_steps WHERE batch_id=? AND position=? AND phase='preflight'",
                            (run["id"], run["position"]),
                        ).fetchone()
                        if before is None or self.clock() - before[0] > 120:
                            raise BridgeError("fresh preflight required")
                    request = self.request(db, run, approved, entry)
                    db.execute(
                        "INSERT INTO reviewed_steps VALUES(?,?,?,?,?)",
                        (run["id"], run["position"], run["phase"], request, self.clock()),
                    )
                    store.event(
                        db,
                        self.clock(),
                        run["actor"],
                        None,
                        "reviewed_request_handed_out",
                        {
                            "batch": run["id"],
                            "position": run["position"],
                            "phase": run["phase"],
                            "hash": digest(request),
                        },
                    )
                    return request
                except (BridgeError, ValueError, KeyError) as exc:
                    self.hold(db, store, run, str(exc))
                    return ""
        return super()._do_sendRequestXML(call)

    def _do_receiveResponseXML(self, call):
        ticket = call.get("ticket") or call.positional(0)
        store = self._locate(ticket)
        response = call.get("response") or call.positional(1)
        with store.transaction() as db:
            run = db.execute("SELECT * FROM reviewed_batches WHERE ticket=?", (ticket,)).fetchone()
            if run:
                if run["state"] == "verified":
                    return 100
                if run["state"] != "active":
                    return -1
                try:
                    approved, entry, connector = self.context(db, store, run)
                    previous = db.execute(
                        "SELECT 1 FROM reviewed_answers WHERE batch_id=? AND response_hash=?",
                        (run["id"], digest(response)),
                    ).fetchone()
                    if previous:
                        return 100 if run["state"] == "verified" else 0
                    sent = db.execute(
                        "SELECT request FROM reviewed_steps WHERE batch_id=? AND position=? AND phase=?",
                        (run["id"], run["position"], run["phase"]),
                    ).fetchone()
                    if sent is None:
                        raise BridgeError("unsolicited reviewed response")
                    db.execute(
                        "INSERT INTO reviewed_answers VALUES(?,?,?,?,?)",
                        (run["id"], run["position"], run["phase"], response, digest(response)),
                    )
                    if call.get("hresult"):
                        raise BridgeError("QuickBooks processor error: " + call.get("hresult"))
                    requests = fromstring(sent[0])[0]
                    root = fromstring(response)
                    if root.tag != "QBXML" or len(root) != 1 or len(root[0]) != len(requests):
                        raise BridgeError("incomplete reviewed response")
                    for rq, rs in zip(requests, root[0], strict=True):
                        if rs.tag != rq.tag[:-2] + "Rs" or rs.get("requestID") != rq.get(
                            "requestID"
                        ):
                            raise BridgeError("response correlation mismatch")
                        if rs.get("statusCode") not in ("0", "1", "500"):
                            raise BridgeError(
                                "QuickBooks rejected request "
                                + rs.get("statusCode", "")
                                + ": "
                                + rs.get("statusMessage", "")
                            )
                    kind = KINDS[entry["operation"]]
                    if run["phase"] != "write":
                        discovery = E.Element("QBXML")
                        msgs = E.SubElement(discovery, "QBXMLMsgsRs")
                        msgs.extend(list(root[0])[:2])
                        self._verify_discovery_response(
                            render(discovery),
                            {
                                "correlation": str(700000000 + run["position"]),
                                "country": "US",
                                "qbxml_version": "17.0",
                            },
                            connector,
                        )
                        rs = root[0][2]
                    else:
                        rs = root[0][0]
                    if run["phase"] == "preflight":
                        self.preflight(db, run, approved, entry, root)
                        db.execute(
                            "UPDATE reviewed_batches SET phase='write' WHERE id=?", (run["id"],)
                        )
                    elif run["phase"] == "write":
                        if rs.get("statusCode") != "0" or len(rs) != 1:
                            raise BridgeError("write outcome is uncertain")
                        verify(entry, approved["maps"], rs[0])
                        db.execute(
                            "UPDATE reviewed_batches SET phase='readback' WHERE id=?", (run["id"],)
                        )
                    else:
                        if rs.get("statusCode") != "0" or len(rs) != 1:
                            raise BridgeError("saved record readback missing")
                        identity = verify(entry, approved["maps"], rs[0])
                        add = db.execute(
                            "SELECT response FROM reviewed_answers WHERE batch_id=? AND position=? AND phase='write'",
                            (run["id"], run["position"]),
                        ).fetchone()
                        original = fromstring(add[0])[0][0][0].findtext(
                            "ListID" if kind == "Customer" else "TxnID"
                        )
                        if identity != original:
                            raise BridgeError("readback identity mismatch")
                        if kind == "ReceivePayment":
                            self.balances(db, run, entry, root, approved)
                        db.execute(
                            "INSERT INTO reviewed_results VALUES(?,?,?,?)",
                            (run["id"], run["position"], identity, canonical(_value(rs[0]))),
                        )
                        store.event(
                            db,
                            self.clock(),
                            run["actor"],
                            None,
                            "reviewed_entry_verified",
                            {"batch": run["id"], "position": run["position"], "id": identity},
                        )
                        done = run["position"] + 1 == len(approved["entries"])
                        db.execute(
                            "UPDATE reviewed_batches SET position=position+1,phase='preflight',state=? WHERE id=?",
                            ("verified" if done else "active", run["id"]),
                        )
                        if done:
                            db.execute("UPDATE control SET paused=1")
                            return 100
                    return 0
                except (BridgeError, ValueError, KeyError, E.ParseError) as exc:
                    self.hold(db, store, run, str(exc))
                    return -1
        return super()._do_receiveResponseXML(call)

    def preflight(self, db, run, approved, entry, root):
        kind = KINDS[entry["operation"]]
        rs = root[0][2]
        allowed = {
            x["txn_id"]: x
            for x in approved.get("accepted_existing", [])
            if x["kind"] == kind and x["ref_number"] == entry.get("source_reference")
        }
        for old in db.execute(
            "SELECT position,record_id FROM reviewed_results WHERE batch_id=?", (run["id"],)
        ):
            old_entry = approved["entries"][old["position"]]
            if (
                old_entry.get("source_reference") == entry.get("source_reference")
                and old_entry["operation"] == entry["operation"]
            ):
                allowed[old["record_id"]] = None
        for saved in rs:
            identity = saved.findtext("ListID" if kind == "Customer" else "TxnID")
            if identity not in allowed:
                raise BridgeError("unapproved existing reference; no duplicate created")
            previous = allowed[identity]
            if previous and (
                saved.findtext("TxnDate") != previous["txn_date"]
                or saved.findtext("CustomerRef/ListID") != previous["customer_id"]
                or Decimal(saved.findtext("TotalAmount", "NaN"))
                != Decimal(previous["total_amount"])
            ):
                raise BridgeError("accepted existing record changed")
        prefs = root[0][3]
        if prefs.get("statusCode") != "0" or len(prefs) != 1:
            raise BridgeError("preferences read failed")
        if prefs[0].findtext("MultiCurrencyPreferences/IsMultiCurrencyOn") not in (None, "false"):
            raise BridgeError("multicurrency requires separate qualification")
        if kind == "ReceivePayment":
            for query, line in zip(
                list(root[0])[4 : 4 + len(entry["allocations"])], entry["allocations"], strict=True
            ):
                if query.get("statusCode") != "0" or len(query) != 1:
                    raise BridgeError("allocation invoice missing")
                invoice = query[0]
                if (
                    invoice.findtext("CustomerRef/ListID")
                    != approved["maps"]["customers"][entry["customer_full_name"]]["ListID"]
                ):
                    raise BridgeError("allocation customer differs")
                needed = Decimal(line["payment_amount"]) + Decimal(line.get("discount_amount", "0"))
                if Decimal(invoice.findtext("BalanceRemaining", "NaN")) < needed:
                    raise BridgeError("allocation exceeds fresh balance")
        offset = 4 + (len(entry["allocations"]) if kind == "ReceivePayment" else 0)
        for rs, master in zip(
            list(root[0])[offset:], self.masters(entry, approved["maps"]), strict=True
        ):
            if rs.get("statusCode") != "0" or len(rs) != 1 or rs[0].tag != master["kind"] + "Ret":
                raise BridgeError("exact reviewed master unavailable")
            saved = _value(rs[0])
            if saved.get("IsActive") != "true":
                raise BridgeError("reviewed master is inactive")
            if master.get("ListID") and saved.get("ListID") != master["ListID"]:
                raise BridgeError("reviewed master identity differs")
            for field in ("Name", "FullName", "Initial", "AccountType"):
                if field in master and master[field] != saved.get(field):
                    raise BridgeError("reviewed master changed: " + field)
            if master["kind"] == "SalesTaxCode" and saved.get("IsTaxable") != "false":
                raise BridgeError("non-tax code is taxable")
            if master["kind"] == "InventorySite" and saved.get("IsActive") != "true":
                raise BridgeError("inventory site inactive")

    def balances(self, db, run, entry, root, approved):
        before = db.execute(
            "SELECT response FROM reviewed_answers WHERE batch_id=? AND position=? AND phase='preflight'",
            (run["id"], run["position"]),
        ).fetchone()
        old = list(fromstring(before[0])[0])[4 : 4 + len(entry["allocations"])]
        current = list(root[0])[3:]
        for a, b, line in zip(old, current, entry["allocations"], strict=True):
            expected = (
                Decimal(a[0].findtext("BalanceRemaining"))
                - Decimal(line["payment_amount"])
                - Decimal(line.get("discount_amount", "0"))
            )
            if len(b) != 1 or Decimal(b[0].findtext("BalanceRemaining", "NaN")) != expected:
                raise BridgeError("saved invoice balance effect differs")
            for target in entry.get("expected_remaining", []):
                if target["invoice_ref_number"] == line[
                    "invoice_ref_number"
                ] and expected != Decimal(target["amount"]):
                    raise BridgeError("reviewed remaining invoice balance differs")
            if (
                "expected_invoice_999_remaining" in entry
                and line["invoice_ref_number"] == "999"
                and expected != Decimal(entry["expected_invoice_999_remaining"])
            ):
                raise BridgeError("reviewed invoice closure not achieved")
