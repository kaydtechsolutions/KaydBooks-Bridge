"""A native error alone cannot clear an uncertain supplier payment."""
# ruff: noqa: F811

import hashlib
import json
import sqlite3
from xml.etree import ElementTree as E

import pytest

from kaydbooks_bridge.config import BridgeError, Config
from kaydbooks_bridge.sample_supplier_payment_posting import post, reconcile
from kaydbooks_bridge.supplier_payments import plan
from test_direct_sdk import direct  # noqa: F401
from test_qbwc_discovery import discovery_setup  # noqa: F401
from test_sample_supplier_payments import queued_payment  # noqa: F401
from test_supplier_payments import payment_case  # noqa: F401


@pytest.mark.parametrize(
    "proof_change", [None, "missing-close", "wrong-request", "wrong-correlation", "different-error"]
)
def test_rejection_requires_original_proof_and_absence(queued_payment, proof_change):
    bridge, token, job_id, session = queued_payment()

    def rejected(request, write, folder, approve):
        allowed = approve(session.xml(request))
        if write is None or not allowed:
            return None
        session.writes += 1
        folder.mkdir(parents=True)
        folder.joinpath("write.request.xml").write_bytes(write.encode())
        folder.joinpath("write-intent.txt").write_text(hashlib.sha256(write.encode()).hexdigest())
        if proof_change != "missing-close":
            folder.joinpath("closed.txt").write_text("closed")
        correlation = E.fromstring(write)[0][0].get("requestID")
        if proof_change == "wrong-correlation":
            correlation += "9"
        code = "3100" if proof_change == "different-error" else "3070"
        answer = f'<QBXML><QBXMLMsgsRs><BillPaymentCheckAddRs requestID="{correlation}" statusCode="{code}" statusSeverity="Error"/></QBXMLMsgsRs></QBXML>'
        folder.joinpath("add.response.xml").write_text(answer)
        if proof_change == "wrong-request":
            folder.joinpath("write-intent.txt").write_text("0" * 64)
        return answer

    with pytest.raises(BridgeError):
        post(bridge, token, "company-a", job_id, exchange=rejected, read_exchange=session.read)
    assert bridge.status(token, "company-a", job_id)["state"] == "unknown"
    if proof_change is not None:
        with pytest.raises(BridgeError):
            reconcile(
                bridge, token, "company-a", job_id, exchange=rejected, read_exchange=session.read
            )
        assert bridge.status(token, "company-a", job_id)["state"] == "unknown"
    else:
        result = reconcile(
            bridge, token, "company-a", job_id, exchange=rejected, read_exchange=session.read
        )
        assert result["state"] == "failed" and result["txn_id"] is None
        _, _, _, store = bridge._context(token, "company-a", "read")
        with store.transaction() as db:
            proof = json.loads(
                db.execute(
                    "SELECT evidence FROM native_supplier_payment_rejections WHERE job_id=?",
                    (job_id,),
                ).fetchone()[0]
            )
            assert proof["status_code"] == "3070" and proof["retry_authorized"] is False
            for action in (
                "UPDATE native_supplier_payment_rejections SET evidence='{}'",
                "DELETE FROM native_supplier_payment_rejections",
            ):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(action)
        with pytest.raises(BridgeError):
            post(bridge, token, "company-a", job_id, exchange=rejected)
    assert session.writes == 1 and bridge.audit(token, "company-a")["valid"]


def test_supplier_reference_native_length_and_legacy_read_boundary(payment_case):
    path, _, payload = payment_case
    policy = Config.load(path).companies["company-a"]
    payload["ref_number"] = "ABCDEFGHIJK"
    assert plan(policy, payload)
    payload["ref_number"] += "L"
    with pytest.raises(BridgeError, match="1-11"):
        plan(policy, payload)
    assert plan(policy, payload, recovering=True)["payment"]["ref_number"] == "ABCDEFGHIJKL"
    from kaydbooks_bridge.supplier_payment_receipt import add_request

    with pytest.raises(BridgeError, match="1-11"):
        add_request(policy, payload, "1234")
