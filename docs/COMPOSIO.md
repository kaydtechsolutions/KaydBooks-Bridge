# Optional Composio integration

Composio is an optional v0.2.0 integration outside Hermes. It does not change the
QuickBooks/QBWC path and is disabled until a private policy, project API key and exact
connected-account IDs are configured.

The policy pins each company to a stable Composio user ID and an allowlist of exact tool
slugs, versions and connected accounts. The adapter calls only the current Composio REST
v3.1 direct-tool endpoint documented in the
[official Execute tool reference](https://docs.composio.dev/reference/api-reference/tools/postToolsExecuteByToolSlug).
It does not expose toolkit discovery, broad proxy calls or Composio session meta-tools.
Local path arguments are rejected.

Read tools may be auto-approved. Write tools require approval by a different principal.
Each request has a company-scoped idempotency key and durable SQLite state. The adapter
commits `in-flight` before the network call. A timeout or ambiguous transport failure
becomes `unknown` and cannot be retried; an operator must inspect the provider before
creating a new request. Stored results retain hashes and provider log IDs rather than
response bodies.

Configure `KAYDBOOKS_COMPOSIO_API_KEY` and connected-account variables in the private
credential file. Replace every example tool version with the version returned for the
authorized Composio project. Never paste keys into chat or commit them. Qualify one read
tool and one independently approved write in a non-production account before marking the
integration gate complete.
