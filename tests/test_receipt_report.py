# ruff: noqa: F811
import json

import pytest

from kaydbooks_bridge import reports
from kaydbooks_bridge.config import BridgeError
from test_receipt_lifecycle import read_saved, receipt_exchange
from test_sample_posting import (  # noqa: F401
    commercial,
    direct,
    discovery_setup,
    receipt_case,
    saved_job,
    setup_invoice,
)


def test_register_sources_totals_export_and_filters(saved_job, tmp_path):
    bridge, token, job_id, _, reference = saved_job
    read_saved(saved_job, exchange=receipt_exchange())
    bridge.attach_receipt(token, "company-a", job_id, reference)
    path = bridge.config_path
    raw = json.loads(path.read_text())
    for principal in raw["principals"].values():
        if "company-a" in principal["companies"]:
            principal["companies"]["company-a"] += ["report", "export"]
    path.write_text(json.dumps(raw))
    result = reports.register(bridge, token, "company-a", "2000-01-01", "2099-12-31")
    assert (
        len(result["rows"]) == 1
        and result["derived"]
        and result["source"] == "historical-verified-receipts"
    )
    assert result["total"] == "10.00" and result["rows"][0]["current_balance_verified"] is False
    assert result["rows"][0]["source_response_sha256"]
    assert reports.register(bridge, token, "company-a", "1999-01-01", "1999-12-31")["rows"] == []
    result = reports.export(
        bridge, token, "company-a", "2000-01-01", "2099-12-31", tmp_path / "report.json"
    )
    assert result["exported"]
    with pytest.raises(BridgeError, match="new private"):
        reports.export(
            bridge, token, "company-a", "2000-01-01", "2099-12-31", tmp_path / "report.json"
        )
    with pytest.raises(BridgeError):
        reports.register(bridge, token, "company-a", "2099-01-01", "2000-01-01")
