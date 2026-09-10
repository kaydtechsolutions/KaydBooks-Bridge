# Remote MCP

KaydBooks Bridge v0.2.0 serves stateless Streamable HTTP at
`https://<private-magicdns-name>/mcp`. Tailscale supplies private HTTPS and Caddy routes
the request to the loopback MCP process.

Every request requires a Bearer token that maps to a Bridge principal. The separate
remote policy then binds that principal to one source (`codex`, `chatgpt` or `whatsapp`),
an exact company list and exact versioned tools. Bridge permissions are checked again
inside every tool. The boundary rejects unexpected Host or Origin values, duplicate
security headers, requests over 1 MiB and rate-limit excess. Logs contain security
metadata only; source documents, accounting payloads and credentials are excluded.

Exposed tools cover company catalogs, reviewed entry preparation/status, batch previews,
QBWC reports and the optional Composio request lifecycle. There is no shell, SQL, raw
qbXML, filesystem, Proxmox or Tailscale tool. WhatsApp policy deliberately omits Composio.

For Codex or ChatGPT, add the private `/mcp` URL and the assigned Bearer token through
that product's secure connector configuration. Keep tokens out of prompts, Git, shell
history and `.qwc` files. Test unauthenticated refusal, authenticated `initialize`,
`tools/list`, a permitted read and a denied company/tool before use.
