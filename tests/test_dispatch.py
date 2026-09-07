"""Dispatch uses the real durable/native contracts with a synthetic QuickBooks session."""

# ruff: noqa: F811
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kaydbooks_bridge import dispatch
from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.direct_sdk import company_lock
from kaydbooks_bridge.sample_posting import post
from kaydbooks_bridge.service import Bridge
from test_direct_sdk import direct  # noqa: F401
from test_invoice_commercial import commercial, response  # noqa: F401
from test_invoice_compatibility import setup_invoice  # noqa: F401
from test_invoice_receipt import receipt_case, saved_receipt  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_receipt_lifecycle import receipt_exchange, saved_job  # noqa: F401
from test_sample_posting import Session, queued  # noqa: F401

UTC = timezone.utc


@pytest.fixture
def ready(queued):
    bridge, token, job, envelope = queued
    path = Path(bridge.config_path)
    raw = json.loads(path.read_text())
    raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].append(
        "manage-workflows"
    )
    path.write_text(json.dumps(raw))
    now = time.time()
    spec = dict(
        mode="scheduled",
        timezone="UTC",
        first_run=datetime.fromtimestamp(now - 1, UTC).isoformat(),
        interval_seconds=60,
        max_runs=3,
        expires_at=now + 1000,
        missed_run="skip",
        grace_seconds=30,
        operations=["invoice.create"],
        sources=[envelope["source"]["namespace"]],
        job_ids=[job],
        max_jobs_per_run=1,
        max_jobs_total=2,
        max_amount_per_job="500.00",
        max_amount_total="1000.00",
    )
    return bridge, token, job, spec


def run(bridge, token, session):
    return dispatch.tick(
        bridge,
        token,
        "company-a",
        dispatchers={
            "invoice.create": lambda b, t, c, j: post(
                b, t, c, j, exchange=session, read_exchange=receipt_exchange()
            )
        },
    )


@pytest.mark.parametrize("mode", ["scheduled", "automatic"])
def test_due_once_restart_and_immutable_plan(ready, mode):
    b, t, j, s = ready
    s.update(mode=mode, job_ids=[j] if mode == "scheduled" else [])
    dispatch.create(b, t, "company-a", "morning", s)
    session = Session()
    assert run(b, t, session)["results"][0]["state"] == "verified"
    restarted = Bridge(b.config_path, clock=b.clock)
    assert run(restarted, t, session)["results"] == []
    assert session.writes == 1 and b.audit(t, "company-a")["valid"]
    _, _, _, store = b._context(t, "company-a", "read")
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM dispatch_claims").fetchone()[0] == 1
        with pytest.raises(Exception, match="immutable"):
            db.execute("UPDATE dispatch_claims SET amount='0.00'")


def test_cancellation_at_native_authorization_and_no_manual_bypass(ready):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)
    session = Session(before=lambda: dispatch.cancel(b, t, "company-a", "morning"))
    assert run(b, t, session)["results"][0]["action"] == "held_for_review"
    assert session.writes == 0
    assert run(b, t, session)["results"] == []
    with pytest.raises(BridgeError, match="original dispatch"):
        post(b, t, "company-a", j, exchange=Session())
    assert dispatch.create(b, t, "company-a", "morning", s)["enabled"] is False


@pytest.mark.parametrize("change", ["permission", "policy", "pause"])
def test_late_revocation_blocks_write(ready, change):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)

    def revoke():
        path = Path(b.config_path)
        raw = json.loads(path.read_text())
        if change == "permission":
            raw["principals"][next(iter(raw["principals"]))]["companies"]["company-a"].remove(
                "manage-workflows"
            )
        elif change == "policy":
            raw["companies"]["company-a"]["max_total"] = "999.00"
        else:
            _, _, _, store = b._context(t, "company-a", "read")
            with store.transaction() as db:
                db.execute("UPDATE control SET paused=1")
        path.write_text(json.dumps(raw))

    session = Session(before=revoke)
    assert run(b, t, session)["results"][0]["action"] == "held_for_review"
    assert session.writes == 0


def test_lost_response_never_resends(ready):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)
    session = Session(crash="after-write")
    assert run(b, t, session)["results"][0]["state"] == "unknown"
    assert run(Bridge(b.config_path, clock=b.clock), t, session)["results"] == []
    assert session.writes == 1


@pytest.mark.parametrize("missed,expected", [("skip", 0), ("coalesce", 1)])
def test_missed_occurrences_are_recorded_without_catchup_storm(ready, missed, expected):
    b, t, j, s = ready
    now = b.clock()
    s.update(first_run=datetime.fromtimestamp(now - 160, UTC).isoformat(), missed_run=missed)
    dispatch.create(b, t, "company-a", "morning", s)
    session = Session()
    run(b, t, session)
    assert session.writes == expected
    occurrences = dispatch.status(b, t, "company-a")["profiles"][0]["occurrences"]
    assert len(occurrences) == 3
    assert all(r["result"]["skipped"] for r in occurrences[:2])


def test_limits_hold_source_without_claim_or_write(ready):
    b, t, j, s = ready
    s.update(max_amount_per_job="0.01")
    dispatch.create(b, t, "company-a", "morning", s)
    session = Session()
    assert run(b, t, session)["results"] == [] and session.writes == 0
    occurrence = dispatch.status(b, t, "company-a")["profiles"][0]["occurrences"][0]
    assert occurrence["result"]["held"] == [
        {"job_id": j, "reason": "dispatch_limit_requires_review"}
    ]
    assert b.status(t, "company-a", j)["state"] == "queued"


def test_no_overlapping_dispatch(ready):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)
    _, _, _, store = b._context(t, "company-a", "read")
    with (
        company_lock(store.path.with_suffix(".dispatch.lock")),
        pytest.raises(BridgeError, match="busy"),
    ):
        run(b, t, Session())


def test_crash_after_plan_resumes_original_claim(ready):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)

    def die(*args):
        raise SystemExit("parent exit before native dispatch")

    with pytest.raises(SystemExit):
        dispatch.tick(b, t, "company-a", dispatchers={"invoice.create": die})
    with pytest.raises(BridgeError, match="original dispatch"):
        post(b, t, "company-a", j, exchange=Session())
    session = Session()
    assert (
        run(Bridge(b.config_path, clock=b.clock), t, session)["results"][0]["state"] == "verified"
    )
    assert session.writes == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"timezone": "America/New_York", "first_run": "2026-07-01T12:00:00-05:00"},
        {"first_run": "2026-09-07T12:00:00"},
        {"interval_seconds": True},
        {"operations": ["raw.xml"]},
        {"sources": ["unassigned"]},
        {"max_amount_total": "NaN"},
        {"expires_at": float("inf")},
        {"grace_seconds": 61},
        {"mode": "automatic"},
    ],
)
def test_invalid_or_ambiguous_rules_rejected(ready, patch):
    b, t, j, s = ready
    s.update(patch)
    with pytest.raises(BridgeError):
        dispatch.create(b, t, "company-a", "morning", s)


def test_cancel_before_due_and_company_isolation(ready):
    b, t, j, s = ready
    dispatch.create(b, t, "company-a", "morning", s)
    dispatch.cancel(b, t, "company-a", "morning")
    session = Session()
    assert run(b, t, session)["results"] == [] and session.writes == 0
    with pytest.raises(BridgeError):
        dispatch.status(b, t, "company-b")
