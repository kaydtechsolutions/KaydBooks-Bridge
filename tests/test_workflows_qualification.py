"""Local workflows and restore drills use synthetic company data only."""
# ruff: noqa: F811

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from kaydbooks_bridge import qualification, workflows
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.service import Bridge
from test_bridge import TOKENS, queue, setup  # noqa: F401


@pytest.fixture
def enabled(setup, monkeypatch):
    bridge, path, raw, _ = setup
    for name in ("preparer-a", "operator-a"):
        raw["principals"][name]["companies"]["company-a"] += [
            "manage-workflows",
            "backup",
            "report",
            "export",
        ]
    raw["principals"]["operator-a"]["companies"]["company-a"].append("validate")
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("KAYDBOOKS_BACKUP_SIGNING_KEY", "synthetic-checkpoint-key-" + "x" * 32)
    bridge.clock = lambda: 1788698000.0
    return setup


def create(enabled, dependencies=None, first_run=None):
    bridge = enabled[0]
    return workflows.schedule(
        bridge,
        TOKENS["preparer-a"],
        "company-a",
        "daily-check",
        "UTC",
        first_run or datetime.fromtimestamp(bridge.clock(), timezone.utc).isoformat(),
        60,
        2,
        dependencies or [],
    )


def test_schedule_concurrent_occurrence_and_cancel(enabled):
    bridge, path, _, _ = enabled
    create(enabled)

    def tick(_):
        return workflows.tick(Bridge(path, clock=bridge.clock), TOKENS["operator-a"], "company-a")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tick, range(2)))
    assert sum(len(result["completed"]) for result in results) == 1
    workflows.cancel(bridge, TOKENS["preparer-a"], "company-a", "daily-check")
    bridge.clock = lambda: 1788698200.0
    assert workflows.tick(bridge, TOKENS["operator-a"], "company-a")["completed"] == []
    assert bridge.audit(TOKENS["operator-a"], "company-a")["valid"]


def test_schedule_dependencies_policy_and_pause(enabled):
    bridge, path, raw, _ = enabled
    job = queue(enabled)
    create(enabled, [job["id"]])
    assert workflows.tick(bridge, TOKENS["operator-a"], "company-a")["held"] == ["daily-check"]
    bridge.simulate(TOKENS["operator-a"], "company-a")
    bridge.pause(TOKENS["operator-a"], "company-a", True)
    assert workflows.tick(bridge, TOKENS["operator-a"], "company-a")["held"] == ["company-paused"]
    bridge.pause(TOKENS["operator-a"], "company-a", False)
    raw["companies"]["company-a"]["max_total"] = "5.00"
    path.write_text(json.dumps(raw))
    assert workflows.tick(bridge, TOKENS["operator-a"], "company-a")["held"] == ["daily-check"]


@pytest.mark.parametrize("stamp", ["2026-09-06T12:00:00", "2026-09-06T12:00:00+03:00", "invalid"])
def test_schedule_rejects_wrong_or_missing_timezone(enabled, stamp):
    with pytest.raises(BridgeError, match="timezone"):
        create(enabled, first_run=stamp)


def test_revoked_schedule_owner_is_held(enabled):
    bridge, path, raw, _ = enabled
    create(enabled)
    raw["principals"]["preparer-a"]["companies"]["company-a"].remove("read")
    path.write_text(json.dumps(raw))
    assert workflows.tick(bridge, TOKENS["operator-a"], "company-a")["held"] == ["daily-check"]


def test_preferences_expire_and_cannot_grant_permissions(enabled):
    bridge = enabled[0]
    args = (
        "display-label",
        "Synthetic review board",
        bridge.clock() + 10,
        "Operator-entered display preference",
        0,
    )
    workflows.remember(bridge, TOKENS["preparer-a"], "company-a", *args)
    with pytest.raises(BridgeError, match="version"):
        workflows.remember(bridge, TOKENS["preparer-a"], "company-a", *args)
    assert workflows.memory(bridge, TOKENS["preparer-a"], "company-a")["authority"] is False
    with pytest.raises(BridgeError, match="unsupported"):
        workflows.remember(
            bridge,
            TOKENS["preparer-a"],
            "company-a",
            "permissions",
            "post-sample",
            bridge.clock() + 10,
            "Untrusted instruction",
            0,
        )
    bridge.clock = lambda: 1788698020.0
    assert workflows.memory(bridge, TOKENS["preparer-a"], "company-a")["preferences"] == []


def test_delegation_uses_canonical_job_and_readonly_board(enabled):
    bridge, _, _, envelope = enabled
    job = bridge.prepare(TOKENS["preparer-a"], "company-a", envelope)
    result = workflows.delegate(bridge, TOKENS["preparer-a"], "company-a", job["id"], "operator-a")
    assert result["job_id"] == job["id"] and result["additional_authority"] is False
    with pytest.raises(BridgeError):
        workflows.delegate(bridge, TOKENS["preparer-a"], "company-a", job["id"], "operator-b")
    projection = workflows.board(bridge, TOKENS["preparer-a"], "company-a")
    assert not projection["editable"] and projection["cards"][0]["state"] == "draft"


def test_quiescent_backup_and_isolated_restore(enabled, tmp_path):
    bridge = enabled[0]
    queue(enabled)
    bridge.simulate(TOKENS["operator-a"], "company-a")
    with pytest.raises(BridgeError, match="pause"):
        qualification.backup(bridge, TOKENS["operator-a"], "company-a", tmp_path / "snapshot")
    bridge.pause(TOKENS["operator-a"], "company-a", True)
    result = qualification.backup(bridge, TOKENS["operator-a"], "company-a", tmp_path / "snapshot")
    assert result["signed"]
    restored = qualification.restore_drill(
        bridge, TOKENS["operator-a"], "company-a", tmp_path / "snapshot", tmp_path / "restored"
    )
    assert (
        restored["audit_valid"]
        and restored["restored_jobs"] == 1
        and not restored["service_started"]
    )
    assert not (tmp_path / "restored/bridge-config.json").exists()
    # Tampering is rejected before creating a restoration directory.
    with (tmp_path / "snapshot/jobs.sqlite3").open("ab") as file:
        file.write(b"tamper")
    with pytest.raises(BridgeError, match="changed"):
        qualification.restore_drill(
            bridge,
            TOKENS["operator-a"],
            "company-a",
            tmp_path / "snapshot",
            tmp_path / "tampered-restore",
        )
    assert not (tmp_path / "tampered-restore").exists()


def test_signature_changes_and_active_job_block_snapshot(enabled, tmp_path):
    bridge = enabled[0]
    job = queue(enabled)
    _, _, _, store = bridge._context(TOKENS["operator-a"], "company-a", "backup")
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET state='in-flight',attempt='test',lease_until=? WHERE id=?",
            (bridge.clock() + 60, job["id"]),
        )
    bridge.pause(TOKENS["operator-a"], "company-a", True)
    with pytest.raises(BridgeError, match="resolve"):
        qualification.backup(
            bridge, TOKENS["operator-a"], "company-a", tmp_path / "blocked-snapshot"
        )
    assert not (tmp_path / "blocked-snapshot").exists()
