# Reporting and delivery

Build the report from normalized independent QuickBooks readbacks, not from request previews or Add acknowledgments alone.

## Financial report

Include company and date; completion and status counts; source prefix, type/reference, amount, status, and TxnID for every financial entry; separately created masters; reconciliation components and difference; requested closing balances; approved corrections; retained exceptions; zero-tax results; posting state; audit result; source hash; and unresolved limitations.

Do not combine credit invoices, receipt cash, payment cash, petty-cash spending, and account-transfer journals into one misleading sales total. Label each category and show only the arithmetic relevant to the requested reconciliation.

## Summary image

Use the same verified figures as the written report. Prefer a compact business infographic with readable text, status counts, source-order summary, reconciliation equation, key closure result, retained follow-up item, and posting state. Inspect the generated image for exact numbers and wording before delivery. Keep the final image in the private evidence directory outside Git.

## External channel delivery

External delivery requires user authorization for the destination. Send through the requested channel only after the final image is verified.

Before sending, hash the local image and verify the copied channel-side file has the same hash. Create a durable destination-specific send claim before calling the channel. Store the conclusive response. If the call times out or returns an uncertain result, record uncertainty and do not resend automatically.

Report the channel message ID when available. Describe it as a send acknowledgment unless the channel separately proves recipient delivery or read status. Channel delivery does not authorize the channel agent to perform the accounting work.
