# Hermes service isolation

Status: the scoped remote client and resumable Hermes service migration are
implemented and tested locally. Installed Linux qualification, tunnel isolation
and the legacy deterministic worker migration are still pending. This document
does not claim the currently deployed service is isolated.

The old gateway joins the `kaydbooks` group to run the local stdio Bridge adapter.
That can expose the shared credentials file and Bridge state to the agent process,
even when its configured tool token has narrower application permissions. Merely
writing a Hermes-only secret file does not remove that OS access.

## Implemented client

`kaydbooks-bridge-remote-client --config PRIVATE_CLIENT_JSON` exposes a stdio MCP
interface to Hermes and forwards only the configured versioned tools through the
existing authenticated HTTPS Remote MCP server. Install the `remote` package extra.
The client needs no Bridge configuration, company database or shared credential file.

Its private configuration contains exactly:

```json
{
  "url": "https://bridge.example/mcp",
  "token_file": "/etc/kaydbooks-hermes/token.json",
  "tools": ["company_catalog_v1", "entry_status_v1", "entry_preview_v1", "batch_status_v1"]
}
```

The token file contains a single `token` key whose value is the separately provisioned
WhatsApp principal credential. Generate it from trusted private configuration locally;
never put its value in a command line, this document, logs or Git. Use a dedicated
directory owned by root with Hermes group traversal, and files owned by root with
Hermes group read access. Hermes must not be able to replace the files.

The destination must be HTTPS on port 443 at `/mcp`, without user information,
query or fragment. TLS verification stays enabled; redirects and inherited HTTP
proxies are disabled. Configuration is trusted local administrator input, never a
field from chat or a document. Tool schemas are filtered to the configured allowlist.
The remote server independently rechecks token, principal, company, tool and workflow
permissions. A configured client tool cannot grant a server permission.

Each tool call opens a fresh authenticated connection and observes token-file
rotation. A transport error returns a generic error and is not automatically retried;
even preparation may create durable jobs. Native remote `isError`, content and
structured content are preserved. No sampling, file roots, arbitrary resources,
shell, SQL or raw qbXML interface is added.

`--check` authenticates and lists available configured tools, then exits. It does
not prove accounting access or perform a transaction. Run a scoped company-catalog
read as a separate acceptance check after migration.

## Required migration sequence

1. Provision the scoped client configuration and token in a dedicated directory;
   verify it against the configured Remote MCP endpoint before changing the gateway.
2. Replace only the KaydBooks MCP entry in the existing Hermes profile, preserving
   model/provider/WhatsApp configuration and other explicitly installed integrations.
3. Remove Bridge-group membership and declared supplementary groups from Hermes.
   Deny the gateway access to Bridge secrets, configuration and state. Remove inherited
   Bridge environment files and local database adapter settings.
4. Give the tunnel its own service group and isolated secret access. It must not gain
   Bridge database or all-principal credentials through a shared group.
5. Migrate any active legacy deterministic worker deliberately; do not give a privileged
   service executable code from a Hermes-writable plugin directory. An inactive legacy
   worker is not evidence that its future activation is safe.
6. Restart the affected processes only after preparation. Verify authenticated catalog
   reads, WhatsApp connection, doctor checks and negative filesystem access as the
   actual service users. Existing jobs/receipts and production-disabled state must stay
   unchanged. No new accounting write is necessary to test this isolation.

Current tests cover actual stdio protocol forwarding through an in-memory MCP session
and the real HTTPS ASGI Remote MCP boundary, wrong-company denial, token rotation,
disabled-principal revocation, tool allowlists, preserved errors/structured results,
bounded configuration/credentials, safe HTTP defaults and no automatic retry.
Installed OS isolation and migration/rerun qualification remain required step-05 work.

## Hermes migration command

After installing a release containing the remote client, run as root on Linux:

```sh
/opt/kaydbooks/current/bin/python -m kaydbooks_bridge.service_isolation \
  --profile /var/lib/hermes/.hermes \
  --server-url https://YOUR-KB-HOST.tailnet.ts.net
```

Use the existing gateway's actual `HERMES_HOME` for `--profile`. The installer
reads that systemd setting and invokes the same migration, preserving provider,
WhatsApp enrollment, other MCP entries, disabled status and narrower tool filters.
Custom KaydBooks wrappers or tools not exposed by the remote policy stop migration
for deliberate compatibility work. They are never silently discarded.

The migration first authenticates with the scoped token and validates the existing
profile. It then stops the gateway, replaces only its KaydBooks entry, removes the
Bridge group and environment-file access, and checks filesystem denial as Hermes.
The user-writable Hermes Python runtime is always executed as Hermes, never root.
Private backups preserve exact original bytes. A concurrent profile edit aborts
publication. Scoped tokens never appear in command arguments or profile JSON.

`/etc/kaydbooks/isolation-hermes.json` saves the restart intent. If a check fails
after the gateway stops, correct the error and rerun the same command: it restores
a previously running gateway only after isolation checks pass. It does not enable
an initially inactive gateway or restore broad credential access on failure.

An active or enabled legacy deterministic worker blocks this migration. An inactive,
disabled worker receives an explicit failing service override, preventing later
accidental use of its old shared-state adapter. Its separate remote-workflow
migration remains a release task; this guard is not feature qualification.
