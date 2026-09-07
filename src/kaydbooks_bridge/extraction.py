"""Immutable offline document observations. Extracted text never authorizes an action."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import documents
from .config import BridgeError
from .direct_sdk import company_lock
from .service import audited
from .validation import canonical, digest

VERSION = 1
MEDIA = {"application/pdf", "image/png", "image/jpeg"}


def runtime():
    node = Path(os.environ.get("KAYDBOOKS_OCR_NODE", ""))
    modules = Path(os.environ.get("KAYDBOOKS_OCR_MODULES", ""))
    if not node.is_absolute() or not node.is_file() or not modules.is_absolute():
        raise BridgeError("configure the private offline OCR runtime first")
    package = modules / "tesseract.js/package.json"
    model = modules / "@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz"
    if not package.is_file() or not model.is_file():
        raise BridgeError("local English OCR package and trained data required")
    version = json.loads(package.read_text(encoding="utf-8"))["version"]
    if version != "7.0.0":
        raise BridgeError("unqualified local OCR version")
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as package_version

    try:
        decoder = {name: package_version(name) for name in ("Pillow", "pypdfium2")}
    except PackageNotFoundError as exc:
        raise BridgeError("install the optional intake dependencies first") from exc
    return {
        "version": VERSION,
        "ocr": version,
        "language": "eng",
        "decoder": decoder,
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "worker_sha256": digest(
            [
                Path(__file__).with_name("extraction_worker.py").read_text(encoding="utf-8"),
                Path(__file__).with_name("ocr_worker.cjs").read_text(encoding="utf-8"),
            ]
        ),
    }


def schema(db):
    documents.schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS document_extractions (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, document_id TEXT NOT NULL,
        result TEXT NOT NULL)""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS extraction_no_{operation.lower()}
            BEFORE {operation} ON document_extractions BEGIN SELECT RAISE(ABORT,'immutable extraction'); END""")

    db.execute("""CREATE TABLE IF NOT EXISTS extraction_jobs (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), extraction_id TEXT NOT NULL REFERENCES document_extractions(id))""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS extraction_job_no_{operation.lower()}
            BEFORE {operation} ON extraction_jobs BEGIN SELECT RAISE(ABORT,'immutable extraction lineage'); END""")


def suggestions(pages):
    """Collect alternatives, never guess date locale, identity, line math or permissions."""
    labels = {
        "reference": r"(?:invoice|bill|credit|receipt)(?: number| no\.?| #)?",
        "date": r"(?:invoice date|bill date|date|transaction date)",
        "due_date": r"due date",
        "customer": r"(?:customer|bill to)",
        "supplier": r"(?:supplier|vendor|from)",
        "total": r"(?:grand total|total|amount due)",
        "currency": r"currency",
    }
    candidates = {key: [] for key in labels}
    for page in pages:
        for line in page["text"].splitlines():
            for field, label in labels.items():
                match = re.fullmatch(r"\s*" + label + r"\s*[:#]\s*(.{1,200}?)\s*", line, re.I)
                if match:
                    candidates[field].append(
                        {
                            "text": match[1],
                            "page": page["page"],
                            "confidence": min(0.99, page["confidence"] / 100),
                        }
                    )
    return candidates


@audited
def extract(bridge, token, company, document_id):
    _, actor, _, store = bridge._context(token, company, "prepare")
    if not isinstance(document_id, str) or not re.fullmatch(r"[a-f0-9]{64}", document_id):
        raise BridgeError("exact captured document required")
    engine = runtime()
    extraction_id = digest([company, actor, document_id, engine])
    with company_lock(store.path.parent / "extraction.lock"):
        with store.transaction() as db:
            schema(db)
            source = db.execute(
                "SELECT * FROM documents WHERE id=? AND owner=?", (document_id, actor)
            ).fetchone()
            if source is None or not store.verify_audit(db):
                raise BridgeError("owned intact source required")
            source_sha = hashlib.sha256(source["bytes"]).hexdigest()
            if digest([source["namespace"], source["reference"], source_sha]) != document_id:
                raise BridgeError("source integrity check failed")
            if source["media_type"] not in MEDIA:
                raise BridgeError("extraction accepts PDF, PNG and JPEG only")
            previous = db.execute(
                "SELECT result FROM document_extractions WHERE id=?", (extraction_id,)
            ).fetchone()
            if previous:
                return json.loads(previous[0])
        with tempfile.TemporaryDirectory(prefix="extract-", dir=store.path.parent) as name:
            directory = Path(name)
            source_file = directory / "source.bin"
            source_file.write_bytes(source["bytes"])
            output = directory / "result.json"
            # Do not pass connector/operator secrets into document decoders or OCR workers.
            environment = {
                k: v
                for k, v in os.environ.items()
                if k.upper()
                in {
                    "SYSTEMROOT",
                    "WINDIR",
                    "PATH",
                    "TEMP",
                    "TMP",
                    "HOME",
                    "USERPROFILE",
                    "KAYDBOOKS_OCR_NODE",
                    "KAYDBOOKS_OCR_MODULES",
                }
            }
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "kaydbooks_bridge.extraction_worker",
                        str(source_file),
                        source["media_type"],
                        str(output),
                    ],
                    env=environment,
                    cwd=directory,
                    capture_output=True,
                    timeout=210,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except subprocess.TimeoutExpired as exc:
                raise BridgeError(
                    "document extraction timed out; source retained for manual review"
                ) from exc
            if completed.returncode or not output.is_file() or output.stat().st_size > 4_000_000:
                raise BridgeError(
                    "document decoding/OCR failed or exceeded limits; source retained for manual review"
                )
            observed = json.loads(output.read_text(encoding="utf-8"))
        _, latest_actor, _, _ = bridge._context(token, company, "prepare")
        if latest_actor != actor or runtime() != engine:
            raise BridgeError("extraction runtime or access changed; review a new extraction")
        result = {
            "extraction_id": extraction_id,
            "namespace": source["namespace"],
            "document_id": document_id,
            "company": company,
            "source_sha256": hashlib.sha256(source["bytes"]).hexdigest(),
            "engine": engine,
            **observed,
            "candidates": suggestions(observed["pages"]),
            "review_required": True,
            "content_is_untrusted": True,
            "accounting_writes": 0,
            "policy_changes": 0,
        }
        result["sha256"] = digest(result)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO document_extractions VALUES (?,?,?,?)",
                (extraction_id, actor, document_id, canonical(result)),
            )
            store.event(
                db,
                bridge.clock(),
                actor,
                None,
                "document_extracted",
                {"extraction_id": extraction_id, "sha256": result["sha256"]},
            )
        return result


@audited
def prepare(
    bridge,
    token,
    company,
    extraction_id,
    extraction_sha256,
    idempotency_key,
    operation,
    payload,
    master_evidence=None,
):
    _, actor, _, store = bridge._context(token, company, "prepare")
    with store.transaction() as db:
        schema(db)
        row = db.execute(
            "SELECT * FROM document_extractions WHERE id=? AND owner=?", (extraction_id, actor)
        ).fetchone()
        if row is None or not store.verify_audit(db):
            raise BridgeError("owned intact extraction required")
        result = json.loads(row["result"])
        if result["sha256"] != extraction_sha256:
            raise BridgeError("reviewed extraction fingerprint differs")
    # Typed corrections are still held for explicit source review; OCR never gives 1.0.
    job = documents.prepare(
        bridge,
        token,
        company,
        result["document_id"],
        idempotency_key,
        payload,
        dict.fromkeys(documents.fields(payload), 0.99),
        master_evidence,
        operation=operation,
    )
    with store.transaction() as db:
        existing = db.execute(
            "SELECT extraction_id FROM extraction_jobs WHERE job_id=?", (job["id"],)
        ).fetchone()
        if existing and existing[0] != extraction_id:
            raise BridgeError("existing draft belongs to a different extraction")
        db.execute("INSERT OR IGNORE INTO extraction_jobs VALUES (?,?)", (job["id"], extraction_id))
        store.event(
            db,
            bridge.clock(),
            actor,
            job["id"],
            "extraction_prepared",
            {"extraction_id": extraction_id, "review_required": True},
        )
    return job
