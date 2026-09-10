# Security policy

Report suspected vulnerabilities privately to the repository owner. Do not open a public
issue containing credentials, company data, QuickBooks identifiers or exploit details.

Supported security fixes target the active v0.2 release branch. Private configuration and
state must stay outside Git. Services bind to loopback behind Tailscale HTTPS, principals
and connectors are company-scoped, and accounting writes require the existing review,
approval, idempotency and readback controls. See `docs/V020_DEPLOYMENT.md` for the deployed
boundary and `docs/REMOTE_MCP.md` for the remote tool boundary.
