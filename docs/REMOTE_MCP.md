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

Codex can use the private `/mcp` URL and its assigned Bearer token through secure MCP
configuration on this Tailscale-connected workstation. Keep tokens out of prompts, Git,
shell history and `.qwc` files. Test unauthenticated refusal, authenticated `initialize`,
`tools/list`, a permitted read and a denied company/tool before use.

For ChatGPT developer-mode testing, use OpenAI Secure MCP Tunnel so the LXC remains
private. Create a tunnel ID and runtime API key in Platform tunnel settings, associate the
target ChatGPT workspace, and install the latest `tunnel-client` release in the LXC. Do
not hard-code a download version or use Tailscale Funnel for this path. Initialize the
dedicated profile as the `kaydbooks-tunnel` user with the private HTTPS MCP URL:

```sh
sudo install -o root -g kaydbooks -m 0640 deploy/openai-tunnel.env.example \
  /etc/kaydbooks/openai-tunnel.env
set -a
. /etc/kaydbooks/openai-tunnel.env
set +a
sudo -u kaydbooks-tunnel env HOME=/var/lib/kaydbooks-tunnel \
  CONTROL_PLANE_API_KEY="$CONTROL_PLANE_API_KEY" \
  CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" tunnel-client init \
  --profile kaydbooks-private-mcp --tunnel-id env:CONTROL_PLANE_TUNNEL_ID \
  --mcp-server-url "http://<private-magicdns-name>/mcp"
sudo -u kaydbooks-tunnel env HOME=/var/lib/kaydbooks-tunnel \
  CONTROL_PLANE_API_KEY="$CONTROL_PLANE_API_KEY" \
  CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" tunnel-client doctor \
  --profile kaydbooks-private-mcp --explain
sudo systemctl enable --now kaydbooks-openai-tunnel
unset CONTROL_PLANE_API_KEY CONTROL_PLANE_TUNNEL_ID
```

Enter the runtime key and tunnel ID only in the root-owned environment file; the profile
keeps environment references instead of copying those values. Store the dedicated
ChatGPT principal as a complete `Bearer <token>` value in
`/etc/kaydbooks/openai-tunnel-mcp-authorization`, owned by `root:kaydbooks` with mode
`0640`, and add this scoped file reference to the generated profile:

```yaml
mcp:
  extra_headers:
    Authorization: "file:/etc/kaydbooks/openai-tunnel-mcp-authorization"
```

In ChatGPT developer mode, create the app with the Tunnel connection, select this tunnel,
and choose **No Auth**. The tunnel client adds the MCP Bearer credential locally; the
credential is never entered into ChatGPT or uploaded to OpenAI. Verify authenticated
`initialize` and `tools/list`, scan the frozen tool list and create the draft app. This
final app creation and any workspace publication require the signed-in administrator.

The tunnel daemon runs inside the same LXC as Caddy. Proxmox maps that container's own
hostname to `127.0.1.1`, so Caddy provides a dedicated HTTP listener on that loopback
address. The tunnel profile uses the hostname without an explicit port, which preserves
the expected HTTP `Host` value. External tailnet clients continue to use private HTTPS
on port 443.

Official references: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
and [ChatGPT developer-mode MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta).
