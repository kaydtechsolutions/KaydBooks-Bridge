"""Crash only a test-owned child, using temporary state and synthetic QB responses."""
# ruff: noqa: F401,F811

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.qbwc_contracts import attempt_count
from kaydbooks_bridge.qbwc_posting import enqueue, recover
from kaydbooks_bridge.service import Bridge
from test_bill_lookup import exact_case
from test_direct_sdk import direct
from test_qbwc_bills import preflight
from test_qbwc_discovery import authenticate, call, discovery_setup, receive
from test_qbwc_invoices import send
from test_qbwc_posting import service
from test_sample_bills import queued_bill, receipt_exchange, saved_bill


def crash_child(config_path, evidence_path, accept_response):
    # Refuse external state: this helper can operate only on its temporary fixture.
    path = Path(config_path).resolve()
    raw = json.loads(path.read_text())
    assert Path(raw["state_root"]).resolve() == path.parent / "state"
    assert set(raw["companies"]) == {"company-a", "company-b"}
    svc = service(Bridge(path), ttl_seconds=5)
    ticket, _ = authenticate(svc)
    assert preflight(svc, ticket) == 25
    request = send(svc, ticket)
    assert "BillAddRq" in request
    response = ET.tostring(
        saved_bill(ET.fromstring(request)[0][0].get("requestID"), operation="BillAdd"),
        encoding="unicode",
    )
    if accept_response:
        assert receive(svc, ticket, response) == 75
    with Path(evidence_path).open("x", encoding="utf-8") as output:
        json.dump({"ticket": ticket, "response": response}, output)
        output.flush()
        os.fsync(output.fileno())
    # Deliberately skip closeConnection, finalizers and normal server cleanup.
    os._exit(86)


@pytest.mark.parametrize("accept_response", [False, True])
def test_bill_recovers_after_real_child_exit(queued_bill, tmp_path, accept_response):
    bridge, token, job, _ = queued_bill
    enqueue(bridge, token, "company-a", job)
    config = json.loads(Path(bridge.config_path).read_text())
    # Inherit only the fixture's Bridge credentials, never private deployment keys.
    env = {key: value for key, value in os.environ.items() if not key.startswith("KAYDBOOKS_")}
    fixture_vars = {value["token_env"] for value in config["principals"].values()}
    fixture_vars.update(
        f"KAYDBOOKS_QBWC_COMPANY_{suffix}_{kind}"
        for suffix in ("A", "B")
        for kind in ("SECRET", "FILE")
    )
    env.update({key: os.environ[key] for key in fixture_vars if key in os.environ})
    env["PYTHONPATH"] = os.pathsep.join(
        str(path) for path in (Path(__file__).parent, Path(__file__).parents[1] / "src")
    )
    evidence = tmp_path / "withheld.json"
    process = subprocess.run(
        [
            sys.executable,
            __file__,
            str(bridge.config_path),
            str(evidence),
            str(int(accept_response)),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 86, process.stderr
    proof = json.loads(evidence.read_text())
    assert ET.fromstring(proof["response"]).findtext(".//TxnID") == "saved-bill"
    bridge = Bridge(bridge.config_path)
    svc = service(bridge)
    store = svc._stores["company-a"]
    with store.transaction() as db:
        expires_at = db.execute(
            "SELECT expires_at FROM qbwc_sessions WHERE ticket=?", (proof["ticket"],)
        ).fetchone()[0]
        assert db.execute(
            "SELECT COUNT(*) FROM qbwc_invoice_responses WHERE phase='write'"
        ).fetchone()[0] == int(accept_response)
    time.sleep(max(0, expires_at - time.time()) + 0.02)
    with store.transaction() as db:
        svc._expire_active(db, store, time.time())
    bridge.pause(token, "company-a", True)
    recover(bridge, token, "company-a", job)
    ticket, _ = authenticate(svc)
    assert preflight(svc, ticket, existing=True) == 75
    request = send(svc, ticket)
    assert "BillAddRq" not in request and "BillToPayQueryRq" in request
    destination = tmp_path / "readback.xml"
    receipt_exchange(request, destination)
    assert receive(svc, ticket, destination.read_text()) == 100
    assert call(svc, "closeConnection", ticket=ticket) == "OK"
    result = bridge.status(token, "company-a", job)
    assert result["state"] == "verified"
    assert result["transaction_receipt"]["receipt"]["txn_id"] == "saved-bill"
    with store.transaction() as db:
        assert attempt_count(db, "bill.create") == 1
        assert (
            db.execute("SELECT COUNT(*) FROM qbwc_invoice_steps WHERE phase='write'").fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM qbwc_invoice_steps s JOIN qbwc_invoice_runs r "
                "ON r.id=s.run_id WHERE r.recovery=1 AND s.phase='write'"
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(BridgeError):
        enqueue(bridge, token, "company-a", job)
    assert bridge.audit(token, "company-a")["valid"]


if __name__ == "__main__":
    crash_child(sys.argv[1], sys.argv[2], bool(int(sys.argv[3])))
