"""One-shot sample-only repair for the former unsupported US journal header memo.

This is not arbitrary transaction editing. The original approved journal, warning
response and monetary baseline remain immutable; only missing line memos can change.
"""

import copy
from xml.etree import ElementTree as E

from qbwc_kit._xml import fromstring

from . import journal_entries as journal
from .config import BridgeError
from .invoice_compatibility import required_id
from .service import Bridge, audited
from .validation import digest


def install_schema(db):
    migrated = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='journal_memo_repair_phase_v1'"
    ).fetchone()
    db.execute("""CREATE TABLE IF NOT EXISTS qbwc_journal_memo_repairs (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id),
        run_id TEXT NOT NULL UNIQUE REFERENCES qbwc_invoice_runs(id),
        actor TEXT NOT NULL, approver TEXT NOT NULL, created_at REAL NOT NULL,
        expires_at REAL NOT NULL, fingerprint TEXT NOT NULL, txn_id TEXT NOT NULL,
        origin_response_hash TEXT NOT NULL)""")
    for action in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS journal_memo_repair_no_{action.lower()}
            BEFORE {action} ON qbwc_journal_memo_repairs
            BEGIN SELECT RAISE(ABORT,'journal repair authorization is immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS journal_memo_repair_insert_guard
        BEFORE INSERT ON qbwc_journal_memo_repairs WHEN NOT EXISTS
        (SELECT 1 FROM jobs j JOIN qbwc_invoice_runs r ON r.job_id=j.id
         WHERE j.id=NEW.job_id AND j.operation='journal.create'
         AND j.state IN ('unknown','posted-unverified') AND j.submitter=NEW.actor
         AND r.id=NEW.run_id AND r.recovery=1 AND r.phase='find'
         AND NEW.approver!=NEW.actor AND NEW.fingerprint=j.fingerprint)
        BEGIN SELECT RAISE(ABORT,'owned held journal and separate repair approval required'); END""")
    if migrated:
        return
    # Normal reconciliation still cannot write. Only this separately authorized,
    # sample-only correction gets a single Mod handoff on the existing transaction.
    db.execute("DROP TRIGGER IF EXISTS qbwc_invoice_phase_guard")
    db.execute("""CREATE TRIGGER qbwc_invoice_phase_guard BEFORE UPDATE OF phase ON qbwc_invoice_runs
        WHEN NOT (OLD.phase=NEW.phase
        OR (OLD.phase='preflight' AND NEW.phase IN ('write','lookup','held'))
        OR (OLD.phase IN ('write','find') AND NEW.phase IN ('lookup','held'))
        OR (OLD.phase='lookup' AND NEW.phase IN ('done','held'))
        OR (OLD.phase='find' AND NEW.phase='write' AND EXISTS
            (SELECT 1 FROM qbwc_journal_memo_repairs WHERE run_id=OLD.id)))
        BEGIN SELECT RAISE(ABORT,'invalid QBWC posting phase'); END""")
    db.execute("DROP TRIGGER IF EXISTS qbwc_invoice_recovery_read_only")
    db.execute("""CREATE TRIGGER qbwc_invoice_recovery_read_only
        BEFORE INSERT ON qbwc_invoice_steps WHEN NEW.phase='write' AND NOT (
            (EXISTS (SELECT 1 FROM qbwc_invoice_runs WHERE id=NEW.run_id AND phase='write' AND recovery=0)
             AND NOT EXISTS (SELECT 1 FROM qbwc_invoice_steps s
                JOIN qbwc_invoice_runs old ON old.id=s.run_id
                JOIN qbwc_invoice_runs current ON current.job_id=old.job_id
                WHERE current.id=NEW.run_id AND s.phase='write'))
            OR (EXISTS (SELECT 1 FROM qbwc_journal_memo_repairs g
                JOIN qbwc_invoice_runs r ON r.id=g.run_id JOIN jobs j ON j.id=g.job_id
                WHERE r.id=NEW.run_id AND r.phase='write' AND r.recovery=1
                AND j.operation='journal.create' AND j.state IN ('unknown','posted-unverified')
                AND g.fingerprint=j.fingerprint AND (SELECT paused FROM control)=1)
                AND instr(NEW.request,'<JournalEntryModRq ')>0
                AND instr(NEW.request,'<JournalEntryAdd')=0
                AND instr(NEW.request,'<Amount>')=0 AND instr(NEW.request,'<AccountRef>')=0))
        BEGIN SELECT RAISE(ABORT,'QBWC write cannot be repeated or used for recovery'); END""")
    db.execute("""CREATE TRIGGER journal_memo_repair_phase_v1
        BEFORE UPDATE OF run_id ON qbwc_journal_memo_repairs
        BEGIN SELECT RAISE(ABORT,'journal repair run is immutable'); END""")


def origin(db, job):
    if job["operation"] != "journal.create" or not job["payload"].get("memo"):
        raise BridgeError("repair requires an approved journal default memo")
    records = db.execute(
        "SELECT p.response,p.response_hash,s.request,s.request_hash FROM qbwc_invoice_responses p "
        "JOIN qbwc_invoice_steps s ON s.run_id=p.run_id AND s.phase='write' "
        "JOIN qbwc_invoice_runs r ON r.id=p.run_id "
        "WHERE r.job_id=? AND r.recovery=0 AND p.phase='write'",
        (job["id"],),
    ).fetchall()
    if len(records) != 1:
        raise BridgeError("exact original journal write evidence required")
    evidence = records[0]
    if (
        digest(evidence["response"]) != evidence["response_hash"]
        or digest(evidence["request"]) != evidence["request_hash"]
    ):
        raise BridgeError("original journal evidence hash differs")
    root = fromstring(evidence["response"])
    if root.tag != "QBXML" or len(root) != 1 or root[0].tag != "QBXMLMsgsRs" or len(root[0]) != 1:
        raise BridgeError("exact original warning response required")
    rs = root[0][0]
    if (
        rs.tag != "JournalEntryAddRs"
        or rs.get("statusCode") != "530"
        or rs.get("statusSeverity") != "Warn"
        or len(rs) != 1
        or rs[0].tag != "JournalEntryRet"
    ):
        raise BridgeError("repair is limited to the original unsupported-field warning")
    request = fromstring(evidence["request"])
    add = request.find("QBXMLMsgsRq/JournalEntryAddRq/JournalEntryAdd")
    if add is None or journal.scalar(add, "Memo") != job["payload"]["memo"]:
        raise BridgeError("original unsupported header memo does not match approved intent")
    row = rs[0]
    return (
        required_id(journal.scalar(row, "TxnID")),
        evidence["response_hash"],
        {
            required_id(journal.scalar(n, "TxnLineID"))
            for n in row
            if n.tag in ("JournalDebitLine", "JournalCreditLine")
        },
    )


@audited
def enqueue(bridge, token, company, job_id, *, approver_token):
    from .qbwc_posting import _idle

    config, actor, policy, store = bridge._context(token, company, "recover")
    approver = config.authenticate(approver_token)
    for permission in ("read", "validate", "post-sample"):
        config.authorize(actor, company, permission)
    config.authorize(approver, company, "approve")
    if approver == actor:
        raise BridgeError("journal memo repair requires separate approval")
    # Commit the explicit repair grant and its run together. Ordinary recover()
    # never grants modification authority.
    with store.transaction() as db:
        _idle(db)
        job = store.job(db, job_id)
        if job["state"] not in ("unknown", "posted-unverified") or job["submitter"] != actor:
            raise BridgeError("owned held journal required")
        if not db.execute("SELECT paused FROM control").fetchone()[0] or not store.verify_audit(db):
            raise BridgeError("journal repair requires paused posting and intact audit")
        if db.execute(
            "SELECT 1 FROM jobs WHERE id!=? AND state IN ('unknown','in-flight','posted-unverified')",
            (job_id,),
        ).fetchone():
            raise BridgeError("another unresolved company write prevents repair")
        if db.execute(
            "SELECT 1 FROM qbwc_journal_memo_repairs WHERE job_id=?", (job_id,)
        ).fetchone():
            raise BridgeError(
                "journal memo repair already authorized; use read-only reconciliation"
            )
        if db.execute(
            "SELECT 1 FROM qbwc_invoice_runs WHERE job_id=? AND phase NOT IN ('held','done')",
            (job_id,),
        ).fetchone():
            raise BridgeError("journal already has a pending run")
        Bridge._approval(config, policy, job)
        native, response_hash, _ = origin(db, job)
        from .qbwc_posting import _run_id

        run = _run_id()
        now = bridge.clock()
        db.execute(
            "INSERT INTO qbwc_invoice_runs(id,job_id,recovery,phase,created_at) VALUES(?,?,1,'find',?)",
            (run, job_id, now),
        )
        db.execute(
            "INSERT INTO qbwc_journal_memo_repairs VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                run,
                actor,
                approver,
                now,
                now + 600,
                job["fingerprint"],
                native,
                response_hash,
            ),
        )
        store.event(
            db,
            now,
            actor,
            job_id,
            "qbwc_journal_memo_repair_authorized",
            {
                "run": run,
                "approver": approver,
                "txn_id": native,
                "expires_at": now + 600,
                "maximum_modifications": 1,
                "scope": "missing-approved-line-memos-only",
            },
        )
        return {"job_id": job_id, "run_id": run, "txn_id": native, "posting_paused": True}


def authorization(service, db, store, run, job, *, writing=False):
    grant = db.execute(
        "SELECT * FROM qbwc_journal_memo_repairs WHERE run_id=?", (run["id"],)
    ).fetchone()
    if grant is None:
        return None
    native, response_hash, _ = origin(db, job)
    if (
        grant["fingerprint"] != job["fingerprint"]
        or grant["actor"] != job["submitter"]
        or grant["txn_id"] != native
        or grant["origin_response_hash"] != response_hash
    ):
        raise BridgeError("journal repair intent changed")
    if writing:
        if (
            not grant["created_at"] <= service.clock() < grant["expires_at"]
            or not db.execute("SELECT paused FROM control").fetchone()[0]
            or job["state"] not in ("unknown", "posted-unverified")
        ):
            raise BridgeError("journal repair window or paused state changed")
        for permission in ("recover", "read", "validate", "post-sample"):
            service.config.authorize(grant["actor"], store.company, permission)
        service.config.authorize(grant["approver"], store.company, "approve")
        if grant["actor"] == grant["approver"]:
            raise BridgeError("separate journal repair reviewer required")
    return grant


def inspect(service, db, store, run, job, policy, connector, grant, response):
    """Validate every original field and balance before planning a memo-only Mod."""
    root = fromstring(response)
    if root.findtext("QBXMLMsgsRs/CompanyQueryRs/CompanyRet/IsSampleCompany") != "true":
        raise BridgeError("memo repair is restricted to a verified QuickBooks sample company")
    rows = root.findall("QBXMLMsgsRs/JournalEntryQueryRs/JournalEntryRet")
    if len(rows) != 1:
        raise BridgeError("one exact saved journal required for repair")
    saved = copy.deepcopy(rows[0])
    _, _, original_ids = origin(db, job)
    observed_ids = set()
    changed = False
    for line in rows[0]:
        if line.tag not in ("JournalDebitLine", "JournalCreditLine"):
            continue
        if any(n.tag not in ("TxnLineID", "AccountRef", "Amount", "Memo") for n in line):
            raise BridgeError("unexpected journal line fields; memo repair refused")
        observed_ids.add(required_id(journal.scalar(line, "TxnLineID")))
        if line.find("Memo") is None:
            E.SubElement(line, "Memo").text = job["payload"]["memo"]
            changed = True
    if observed_ids != original_ids:
        raise BridgeError("original journal line identities changed")
    discovery, receipt = journal.validate_lookup(
        E.tostring(root), run["id"] + "7", policy, job["payload"], grant["txn_id"]
    )
    service._verify_discovery_response(
        discovery,
        {"correlation": run["id"] + "7", "country": "US", "qbxml_version": "17.0"},
        connector,
    )
    from .sample_journal_posting import check_preflight

    baseline = db.execute(
        "SELECT p.response,r.id FROM qbwc_invoice_responses p JOIN qbwc_invoice_runs r ON r.id=p.run_id "
        "WHERE r.job_id=? AND r.recovery=0 AND p.phase='preflight' AND p.result=25",
        (job["id"],),
    ).fetchall()
    if len(baseline) != 1:
        raise BridgeError("original journal monetary baseline missing")
    collision, before = check_preflight(
        baseline[0]["response"], policy, job["payload"], connector, baseline[0]["id"]
    )
    if collision is not None:
        raise BridgeError("original journal had a preflight collision")
    journal.verify_balance_effect(job["payload"], before, receipt["balances"], policy=policy)
    return saved, changed


def request(service, db, store, run, job, policy, connector, grant):
    if run["phase"] == "find":
        return journal.append_lookup(
            service._discovery_request(run["id"] + "7", "17.0"),
            run["id"] + "7",
            policy,
            job["payload"],
            grant["txn_id"],
        )
    if run["phase"] != "write":
        raise BridgeError("invalid journal repair request phase")
    authorization(service, db, store, run, job, writing=True)
    previous = db.execute(
        "SELECT p.response,p.response_hash,s.sent_at FROM qbwc_invoice_responses p "
        "JOIN qbwc_invoice_steps s ON s.run_id=p.run_id AND s.phase='find' "
        "WHERE p.run_id=? AND p.phase='find' AND p.result=25",
        (run["id"],),
    ).fetchone()
    if (
        previous is None
        or not 0 <= service.clock() - previous["sent_at"] < 120
        or digest(previous["response"]) != previous["response_hash"]
    ):
        raise BridgeError("fresh intact journal repair preflight required")
    saved, changed = inspect(
        service, db, store, run, job, policy, connector, grant, previous["response"]
    )
    if not changed:
        raise BridgeError("journal memos already match; no modification required")
    root = E.Element("QBXML")
    rq = E.SubElement(
        E.SubElement(root, "QBXMLMsgsRq", onError="stopOnError"),
        "JournalEntryModRq",
        requestID=run["id"] + "998",
    )
    row = E.SubElement(rq, "JournalEntryMod")
    for field in ("TxnID", "EditSequence"):
        E.SubElement(row, field).text = journal.scalar(saved, field)
    for line in saved:
        if line.tag in ("JournalDebitLine", "JournalCreditLine"):
            node = E.SubElement(row, "JournalLineMod")
            E.SubElement(node, "TxnLineID").text = journal.scalar(line, "TxnLineID")
            E.SubElement(node, "Memo").text = line.findtext("Memo") or job["payload"]["memo"]
    return journal.render(root)


def receive(service, db, store, run, job, policy, connector, grant, response):
    if run["phase"] == "find":
        _, changed = inspect(service, db, store, run, job, policy, connector, grant, response)
        db.execute(
            "UPDATE qbwc_invoice_runs SET phase=?,txn_id=? WHERE id=?",
            ("write" if changed else "lookup", grant["txn_id"], run["id"]),
        )
        return 25 if changed else 75
    if run["phase"] == "write":
        root = fromstring(response)
        if (
            root.tag != "QBXML"
            or len(root) != 1
            or root[0].tag != "QBXMLMsgsRs"
            or len(root[0]) != 1
            or root[0][0].tag != "JournalEntryModRs"
        ):
            raise BridgeError("exact journal memo modification response required")
        root[0][0].tag = "JournalEntryQueryRs"
        receipt = journal.validate_receipt(
            E.tostring(root), policy, job["payload"], run["id"] + "998", txn_id=grant["txn_id"]
        )
        _, _, ids = origin(db, job)
        if set(receipt["line_ids"]) != ids:
            raise BridgeError("journal line identities changed during memo repair")
        db.execute(
            "UPDATE qbwc_invoice_runs SET phase='lookup',txn_id=? WHERE id=?",
            (grant["txn_id"], run["id"]),
        )
        store.event(
            db,
            service.clock(),
            grant["actor"],
            job["id"],
            "qbwc_journal_memos_modified",
            {"txn_id": grant["txn_id"], "run": run["id"]},
        )
        return 75
    raise BridgeError("invalid journal repair response phase")
