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
    native_table: str | None
    add_operation: str
    balance_key: str | None = None

    def module(self, kind):
        return import_module("kaydbooks_bridge." + getattr(self, kind))

    def require(self, *args):
        return getattr(self.module("evidence"), self.require_name)(*args)

    def check_preflight(self, *args, recovering=False):
        module = self.module("posting")
        if self.name == "invoice" or self.balance_key:
            result = module.check_preflight(*args, recovering=recovering)
            return result[0] if self.balance_key else result
        return module.check_preflight(*args)

    def append_lookup(self, discovery, run, txn_id, policy, payload):
        module = self.module("receipt")
        if self.balance_key:
            return module.append_lookup(discovery, run, policy, payload, txn_id)
        return module.append_lookup(discovery, run, txn_id, policy, payload)

    def inventory(self, policy, payload):
        if self.balance_key:
            return {}
        if self.name == "invoice":
            return self.module("receipt").inventory_specs(policy, payload)
        return self.module("posting").plan(policy, payload)["binding"].get("inventory_items", {})

    def verify_balances(self, policy, payload, before, after):
        module = self.module("receipt")
        if self.name in ("sales_receipt", "journal", "check", "inventory_transfer"):
            return module.verify_balance_effect(payload, before, after, policy=policy)
        if self.name == "credit":
            return module.verify_balance_effect(
                payload, before, after, inventory=module.plan(policy, payload).get("inventory", {})
            )
        return module.verify_balance_effect(payload, before, after)


CONTRACTS = {
    "check.create": Contract(
        "check",
        "sample_check_posting",
        "checks",
        "check_evidence",
        "require",
        "sample_check_posting",
        "max_checks",
        None,
        "CheckAdd",
        "balances",
    ),
    "inventory-transfer.create": Contract(
        "inventory_transfer",
        "sample_inventory_transfer_posting",
        "inventory_transfers",
        "inventory_transfer_evidence",
        "require",
        "sample_inventory_transfer_posting",
        "max_transfers",
        None,
        "TransferInventoryAdd",
        "balances",
    ),
    "journal.create": Contract(
        "journal",
        "sample_journal_posting",
        "journal_entries",
        "journal_evidence",
        "require",
        "sample_journal_posting",
        "max_entries",
        None,
        "JournalEntryAdd",
        "balances",
    ),
    "sales-receipt.create": Contract(
        "sales_receipt",
        "sample_sales_receipt_posting",
        "sales_receipts",
        "sales_receipt_evidence",
        "require",
        "sample_sales_receipt_posting",
        "max_receipts",
        None,
        "SalesReceiptAdd",
        "balances",
    ),
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
    "customer-payment.create": Contract(
        "payment",
        "sample_payment_posting",
        "payment_receipt",
        "payment_evidence",
        "require",
        "sample_payment_posting",
        "max_payments",
        "native_payment_attempts",
        "ReceivePaymentAdd",
        "invoice_balances",
    ),
    "supplier-payment.create": Contract(
        "supplier_payment",
        "sample_supplier_payment_posting",
        "supplier_payment_receipt",
        "supplier_payment_evidence",
        "require",
        "sample_supplier_payment_posting",
        "max_payments",
        "native_supplier_payment_attempts",
        "BillPaymentCheckAdd",
        "bill_balances",
    ),
    "customer-credit.create": Contract(
        "credit",
        "sample_credit_posting",
        "customer_credits",
        "credit_evidence",
        "require",
        "sample_credit_posting",
        "max_credits",
        "native_credit_attempts",
        "CreditMemoAdd",
        "balances",
    ),
    "supplier-credit.create": Contract(
        "supplier_credit",
        "sample_supplier_credit_posting",
        "supplier_credits",
        "supplier_credit_evidence",
        "require",
        "sample_supplier_credit_posting",
        "max_credits",
        "native_supplier_credit_attempts",
        "VendorCreditAdd",
        "balances",
    ),
}

OPERATIONS_SQL = ",".join("'" + operation + "'" for operation in CONTRACTS)


def contract(operation):
    try:
        return CONTRACTS[operation]
    except KeyError as exc:
        raise BridgeError("operation has no qualified QBWC implementation") from exc


def attempt_count(db, operation):
    adapter = contract(operation)
    native = f"(SELECT COUNT(*) FROM {adapter.native_table})" if adapter.native_table else "0"
    return db.execute(
        f"SELECT {native} + "
        "(SELECT COUNT(*) FROM qbwc_invoice_attempts a JOIN jobs j ON j.id=a.job_id "
        "WHERE j.operation=?)",
        (operation,),
    ).fetchone()[0]
