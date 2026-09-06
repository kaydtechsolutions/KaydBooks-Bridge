"""Signed, quiescent company snapshots and isolated restore drills; no live restore."""

import argparse
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .config import BridgeError, company_policy_context, outside_repository
from .direct_sdk import company_lock
from .sample_posting import save
from .service import Bridge, audited
from .store import Store
from .validation import canonical, digest


def key():
    value = os.environ.get("KAYDBOOKS_BACKUP_SIGNING_KEY", "")
    if len(value) < 32:
        raise BridgeError("private backup signing key required")
    return value.encode()


def signature(value):
    return hmac.new(key(), canonical(value).encode(), hashlib.sha256).hexdigest()


def context(config, policy):
    company = company_policy_context(policy)
    return digest(
        {
            "company": company,
            "connectors": {
                name: asdict(connector)
                for name, connector in config.connectors.items()
                if connector.company == policy.id
            },
        }
    )


def fresh_destination(value, store):
    target = outside_repository(Path(value))
    if (
        target.exists()
        or not target.parent.is_dir()
        or store.path.parent == target
        or store.path.parent in target.parents
    ):
        raise BridgeError("new isolated backup destination required")
    return target


def hash_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise BridgeError("unsupported backup file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@audited
def backup(bridge, token, company, destination):
    config, actor, policy, store = bridge._context(token, company, "backup")
    config.authorize(actor, company, "read")
    key()
    target = fresh_destination(destination, store)
    with company_lock(store.path.with_suffix(".sdk.lock")), store.transaction() as db:
        if not db.execute("SELECT paused FROM control").fetchone()[0]:
            raise BridgeError("pause company before a qualification snapshot")
        if (
            not store.verify_audit(db)
            or db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('in-flight','unknown','posted-unverified')"
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM sdk_discovery WHERE state IN ('prepared','dispatched')"
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM qbwc_sessions WHERE state IN ('authenticated','request-sent','verified','blocked')"
            ).fetchone()
        ):
            raise BridgeError("resolve active sessions and uncertain writes before snapshot")
        files = [
            path
            for path in store.path.parent.rglob("*")
            if path.is_file()
            and path.name not in {"jobs.sqlite3", "jobs.sqlite3-wal", "jobs.sqlite3-shm"}
            and not path.name.endswith(".lock")
        ]
        if (
            any(path.is_symlink() for path in store.path.parent.rglob("*"))
            or sum(path.stat().st_size for path in files) + store.path.stat().st_size
            > 512 * 1024 * 1024
        ):
            raise BridgeError("snapshot resource limit or symlink")
        target.mkdir()
        # A separate read connection can snapshot while this connection holds the writer fence.
        source = sqlite3.connect(store.path)
        destination_db = sqlite3.connect(target / "jobs.sqlite3")
        try:
            source.backup(destination_db)
        finally:
            source.close()
            destination_db.close()
        for path in files:
            dest = target / path.relative_to(store.path.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        hashes = {
            path.relative_to(target).as_posix(): hash_file(path)
            for path in target.rglob("*")
            if path.is_file()
        }
        last = db.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        manifest = {
            "schema_version": 1,
            "company": company,
            "context_sha256": context(config, policy),
            "audit_head": last[0] if last else "0" * 64,
            "files": hashes,
            "created_at": bridge.clock(),
            "scope": "private company state only; credentials/config excluded",
        }
        save(
            target / "manifest.json",
            canonical({"manifest": manifest, "signature": signature(manifest)}),
        )
    with store.transaction() as db:
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "qualification_backup_created",
            {"manifest_sha256": digest(manifest), "audit_head": manifest["audit_head"]},
        )
    return {"manifest_sha256": digest(manifest), "files": len(hashes), "signed": True}


@audited
def restore_drill(bridge, token, company, snapshot, destination):
    config, actor, policy, store = bridge._context(token, company, "backup")
    config.authorize(actor, company, "read")
    source = outside_repository(Path(snapshot))
    manifest_path = source / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 1024 * 1024:
        raise BridgeError("invalid snapshot manifest")
    envelope = json.loads(manifest_path.read_text())
    manifest = envelope["manifest"]
    if (
        not hmac.compare_digest(signature(manifest), envelope["signature"])
        or manifest["schema_version"] != 1
        or manifest["company"] != company
        or manifest["context_sha256"] != context(config, policy)
    ):
        raise BridgeError("snapshot signature/company/policy mismatch")
    hashes = manifest["files"]
    if (
        not isinstance(hashes, dict)
        or not 1 <= len(hashes) <= 10000
        or "jobs.sqlite3" not in hashes
    ):
        raise BridgeError("invalid snapshot file inventory")
    for name, expected in hashes.items():
        part = PurePosixPath(name)
        if (
            part.is_absolute()
            or any(segment in ("..", ".") for segment in part.parts)
            or "\\" in name
            or ":" in name
            or not name
            or (source / name).resolve().is_relative_to(source.resolve()) is False
        ):
            raise BridgeError("unsafe snapshot path")
        if hash_file(source / name) != expected:
            raise BridgeError("snapshot evidence changed")
    if sum((source / name).stat().st_size for name in hashes) > 512 * 1024 * 1024:
        raise BridgeError("snapshot resource limit")
    target = fresh_destination(destination, store)
    target.mkdir()
    restored_company = target / company
    restored_company.mkdir()
    for name in hashes:
        dest = restored_company / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
        if hash_file(dest) != hashes[name]:
            raise BridgeError("restored file differs")
    restored = Store(target, company)
    with restored.transaction() as db:
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone()
            or not restored.verify_audit(db)
            or not db.execute("SELECT paused FROM control").fetchone()[0]
        ):
            raise BridgeError("restored database validation failed")
        last = db.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        if (last[0] if last else "0" * 64) != manifest["audit_head"]:
            raise BridgeError("restored audit checkpoint differs")
        jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    # No runtime config is emitted; the restored directory cannot launch a connector.
    result = {
        "restored_jobs": jobs,
        "integrity": "ok",
        "audit_valid": True,
        "paused": True,
        "service_started": False,
        "production_enabled": False,
    }
    save(target / "restore-proof.json", canonical(result))
    with store.transaction() as db:
        store.event(
            db,
            bridge.clock(),
            actor,
            None,
            "qualification_restore_drill",
            {"manifest_sha256": digest(manifest), **result},
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private snapshot or isolated restore drill")
    parser.add_argument("--config", default=os.environ.get("KAYDBOOKS_CONFIG"))
    parser.add_argument("--company", required=True)
    parser.add_argument("action", choices=["backup", "restore-drill"])
    parser.add_argument("destination")
    parser.add_argument("--snapshot")
    args = parser.parse_args(argv)
    try:
        if not args.config:
            raise BridgeError("private config required")
        bridge = Bridge(args.config)
        token = os.environ.get("KAYDBOOKS_TOKEN", "")
        result = (
            backup(bridge, token, args.company, args.destination)
            if args.action == "backup"
            else restore_drill(bridge, token, args.company, args.snapshot, args.destination)
        )
        print(json.dumps(result))
        return 0
    except (BridgeError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "qualification rejected; inspect private evidence"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
