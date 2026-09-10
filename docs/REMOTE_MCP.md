# Remote MCP

KaydBooks Bridge v0.2.0 serves stateless Streamable HTTP at
`https://<private-magicdns-name>/mcp`. Tailscale supplies private HTTPS and Caddy routes
the request to the loopback MCP process.

Every request requires a Bearer token that maps to a Bridge principal. The separate
remote policy then binds that principal to one source (`codex`, `chatgpt`, `claude`, `gemini` or `whatsapp`),
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
  --profile kaydbooks-private-mcp --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --mcp-server-url "https://<private-magicdns-name>/mcp"
```

The `init --tunnel-id` option expects the actual tunnel ID; it rejects an `env:`
reference. After initialization, edit
`/var/lib/kaydbooks-tunnel/.config/tunnel-client/kaydbooks-private-mcp.yaml`
to use these runtime references. Preserve the generated configuration's other fields:

```yaml
control_plane:
  tunnel_id: "env:CONTROL_PLANE_TUNNEL_ID"
  api_key: "env:CONTROL_PLANE_API_KEY"
health:
  listen_addr: "127.0.0.1:8090"
admin_ui:
  open_browser: false
```

Port 8080 is already used by the Bridge. The tunnel's health listener must use a
different free loopback port. Use 8090 to match `kaydbooks-doctor`.

Keep the runtime key in the root-owned environment file. Store the dedicated
ChatGPT principal as a complete `Bearer <token>` value in
`/etc/kaydbooks/openai-tunnel-mcp-authorization`, owned by `root:kaydbooks` with mode
`0640`, and add this scoped file reference to the generated profile:

```yaml
mcp:
  extra_headers:
    Authorization: "file:/etc/kaydbooks/openai-tunnel-mcp-authorization"
```

Then verify the profile and start the service:

```sh
sudo -u kaydbooks-tunnel env HOME=/var/lib/kaydbooks-tunnel \
  CONTROL_PLANE_API_KEY="$CONTROL_PLANE_API_KEY" \
  CONTROL_PLANE_TUNNEL_ID="$CONTROL_PLANE_TUNNEL_ID" tunnel-client doctor \
  --profile kaydbooks-private-mcp --explain
sudo systemctl enable --now kaydbooks-openai-tunnel
unset CONTROL_PLANE_API_KEY CONTROL_PLANE_TUNNEL_ID
curl --fail --silent --show-error http://127.0.0.1:8090/readyz
```

In ChatGPT developer mode, create the app with the Tunnel connection, select this tunnel,
and choose **No Auth**. The tunnel client adds the MCP Bearer credential locally; the
credential is never entered into ChatGPT or uploaded to OpenAI. Verify authenticated
`initialize` and `tools/list`, scan the frozen tool list and create the draft app. This
final app creation and any workspace publication require the signed-in administrator.

Fresh automatic installations use the private HTTPS MCP URL on port 443. Verify that
the hostname resolves inside the LXC before starting the tunnel. Older deployments may
have a dedicated HTTP listener on `127.0.1.1`; use that route only after verifying its
Caddy configuration. It is not created by every installation.

After connecting the app in ChatGPT, call `company_catalog_v1` for its assigned company
and an unassigned company. The first must succeed and the second must be denied. The
published MCP tool catalog describes the full contract; the remote policy and Bridge
permissions enforce the tools each principal can actually call. A connected app or a
successful catalog read does not verify accounting posting or transaction readback.

Official references: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
and [ChatGPT developer-mode MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta).
