"""Fixed transaction contracts for the shared QBWC lifecycle; no arbitrary qbXML."""

from dataclasses import dataclass
from importlib import import_module

from .config import BridgeError


@dataclass(frozen=True)
class Contract:
    name: str
    posting: str
    receipt: str
    evidence: str
    require_name: str
    settings: str
    limit: str
    native_table: str
    add_operation: str

    def module(self, kind):
        return import_module("kaydbooks_bridge." + getattr(self, kind))

    def require(self, *args):
        return getattr(self.module("evidence"), self.require_name)(*args)

    def check_preflight(self, *args, recovering=False):
        module = self.module("posting")
        if self.name == "invoice":
            return module.check_preflight(*args, recovering=recovering)
        return module.check_preflight(*args)

    def inventory(self, policy, payload):
        if self.name == "invoice":
            return self.module("receipt").inventory_specs(policy, payload)
        return self.module("posting").plan(policy, payload)["binding"].get("inventory_items", {})


CONTRACTS = {
    "invoice.create": Contract(
        "invoice",
        "sample_posting",
        "invoice_receipt",
        "invoice_evidence",
        "require",
        "sample_posting",
        "max_invoices",
        "native_invoice_attempts",
        "InvoiceAdd",
    ),
    "bill.create": Contract(
        "bill",
        "sample_bill_posting",
        "bill_receipt",
        "bills",
        "require_context",
        "sample_bill_posting",
        "max_bills",
        "native_bill_attempts",
        "BillAdd",
    ),
}


def contract(operation):
    try:
        return CONTRACTS[operation]
    except KeyError as exc:
        raise BridgeError("operation has no qualified QBWC implementation") from exc


def attempt_count(db, operation):
    adapter = contract(operation)
    return db.execute(
        f"SELECT (SELECT COUNT(*) FROM {adapter.native_table}) + "
        "(SELECT COUNT(*) FROM qbwc_invoice_attempts a JOIN jobs j ON j.id=a.job_id "
        "WHERE j.operation=?)",
        (operation,),
    ).fetchone()[0]
