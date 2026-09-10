# Fixed supplier-bill discounts and charges

Expense and purchased-service bills accept optional reviewed `adjustments`. Each
entry specifies `kind` (`discount` or `charge`), `scope` (`document` or `line`), a
private mapped `expense_id`, and a positive two-decimal `amount`. Line scope also
requires an original one-based `line_number`. The original bill lines are preserved.

Use a single document discount or one discount per selected line. Discounts cannot
exceed their selected basis, the net bill must remain positive, and both company
and dispatch limits count gross originals plus charges. An adjusted bill may have
at most 100 total native lines. Inventory adjustments are rejected until acquisition
cost allocation is qualified; they must not be silently charged to an expense account.

Fresh checks bind every adjustment account, even an account used only by an
adjustment, to an active Expense/OtherExpense account in the verified single-currency
company. The browser displays scope, account, amount and the resulting signed entries.
Changes invalidate checks and approval. Approval, submission and native posting are
separate actions with current authority checked again before dispatch.

Native bill requests add ordinary expense entries: negative for discounts, positive
for charges. Fixed scope memos identify the original line or whole document. These
entries follow original expenses and precede purchased-service item entries, matching
the SDK aggregate order. Exact native field definitions come from Intuit's
[BillAdd reference](https://developer.intuit.com/app/developer/qbdesktop/docs/api-reference/qbdesktop/billadd).
No raw memo/native XML interface is exposed.

Independent saved-record verification compares every original/adjustment account,
amount, scope memo and total. A separate vendor/AP-scoped BillToPay read must match
the net outstanding amount. A lost response stays uncertain until reconciliation;
it cannot be posted again. Supplier-credit document adjustments remain a separate
contract. This implementation does not enable tax or production posting.

## Installed sample qualification

Two separately approved bills each saved USD10 of original lines, a USD1 discount
and a USD3 charge, for USD12 net. One used document scope with expense lines; the
other used line scope with mixed expense and purchased-service lines. Both saved
exact signed adjustment accounts/memos and independent outstanding balances on
their first attempts. Repeat posting was refused. Vendor Balance Summary increased
by USD24 and customer balance was unchanged.

Desktop/mobile review displayed requested scope and saved signed entries without
offering reposting. Signed isolated restore preserved 55 jobs and 2,282 files,
all 16 master attempts and evidence links, valid audit/integrity, and no pending
master reads. It remained paused without starting the restored service. The bounded
sample bill quota is exhausted. The full browser/OCR-enabled suite passed 1,240
tests; lint and build passed. Exact identities and receipts remain private.
