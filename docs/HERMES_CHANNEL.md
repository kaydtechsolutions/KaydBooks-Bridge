# Hermes reviewed-batch channel (development candidate)

Current verified v0.1.0 acceptance is **13/15 (87%)**. The eight basic entry types,
real Linux-to-Windows connection, exact native operator confirmation, sample invoice
dispatch/readback, native upload/preparation and WhatsApp batch result passed. The
per-company walkthrough and final installation/recovery qualification remain open.
This document describes the implementation; it does not certify production readiness.

## Data and approval flow

1. Hermes captures the original source with `capture_document_v1`, reads mappings
   with `company_catalog_v1`, and obtains fresh QBWC evidence with `qbwc_entry_v1`.
   Resolve ambiguous fields with the operator; never invent extraction certainty.
2. Prepare and validate entries, then call `batch_preview_v1` with one to ten jobs.
   The Bridge stores an immutable manifest of company policy, source hashes,
   job fingerprints, exact payloads and preview hashes. Oversized batches are refused.
3. The trusted worker claims a preview delivery, sends the exact text to the
   privately configured operator DM and records all returned provider message IDs.
4. The operator replies `/kb-confirm <batch-id> <code>` within 15 minutes. The Hermes
   plugin consumes the original native body before model processing. It checks the
   platform, chat, sender, event ID and absence of media or owner-generated content.
   Attachments, forwarded instructions inside documents and model-generated approval
   do not supply confirmation. The Windows side independently checks these facts,
   the signed request, the sent preview, expiry and unchanged policy/job fingerprints.
5. The worker approves under the separate reviewer principal and submits one entry
   at a time. Existing pause, company binding, sample quotas and readback checks still
   apply. It never unpauses posting or retries an unknown accounting write.
6. A deterministic result lists each reference, operation and actual state. Successful
   Add responses awaiting readback are pending. Only verified readback is success.

Delivery is at most one automatic send attempt per preview/result. Provider IDs are
retained locally before acknowledging them to Windows; a lost SSH acknowledgment
retries only that acknowledgment. A lost HTTP response leaves delivery uncertain
for operator inspection. An administrator can record retained explicit human receipt
evidence with `observe_preview`; this neither fabricates a provider ID nor approves
accounting. The exact native-channel confirmation is still required. This
is not exactly-once delivery or a handset read receipt. Retrying a notification
must never repost accounting entries. Expired reviews require fresh validation/review.

## Windows private setup

Use the same config, runtime and physical state directory as the running QBWC service.
Keep a fixed PowerShell launcher outside Git that sets `KAYDBOOKS_CONFIG`,
`KAYDBOOKS_TOOL_SECRET_FILE`, and `KAYDBOOKS_CHANNEL_CONFIG`, sets UTF-8 console/Python
I/O, then invokes the installed Python with `-m kaydbooks_bridge.hermes_channel`.
The process reads one signed JSON request from stdin and emits one JSON response.
The launcher also accepts a `[switch]$Clock` parameter and, when set, invokes
the module with `--clock`. The client uses this read-only authenticated SSH clock
query plus elapsed monotonic time to sign requests despite different host clocks.

Example channel config (replace every example privately):

```json
{
  "company": "company-a",
  "chat_id": "operator-id@lid",
  "sender_ids": ["operator-id@lid"],
  "operator_token_env": "KAYDBOOKS_OPERATOR_SECRET",
  "reviewer_token_env": "KAYDBOOKS_REVIEWER_SECRET",
  "signing_secret_env": "KAYDBOOKS_CHANNEL_SIGNING_SECRET",
  "channel_secret_file": "C:\\BridgePrivate\\channel-secrets.json",
  "allow_outbound": false,
  "outbound_batch_ids": []
}
```

The secret file contains `KAYDBOOKS_CHANNEL_SIGNING_SECRET` with a newly generated
secret of at least 32 characters. Existing Bridge credentials stay on Windows.
Set `approval_required=true`, `allow_self_approval=false` and separate principals.
The operator needs preparation/validation/submit/read/sample-post permissions; the
reviewer needs approval permission. Do not expose that reviewer credential to MCP.

## Linux Hermes setup

Run only one polling gateway per paired WhatsApp inbox. Two independent gateways
sharing the same local bridge race to consume messages; a request can reach the
wrong profile and its unrelated model credentials. When sharing one paired account,
use the installed Hermes version's supported profile routing/multiplexing in one
primary gateway, with an explicit operator chat route to the intended profile.
Disable the competing standalone profile gateway and its second transport. Retain
the working session path and existing default/home-chat route. Back up configuration
and sessions before an idle restart, and verify both routing and WhatsApp health.
Do not copy pairing credentials into a second active WhatsApp session.

The optional `kaydbooks-bridge-setup hermes` command generates private fixed launchers
and disabled channel/MCP configuration from an existing company deployment; see
[the setup request and command](INSTALL_HERMES_DATA_ENTRY.md#4-connect-the-existing-linux-hermes-installation).

Identify the actual gateway profile handling the chosen chat first. Copy
`integrations/hermes/kaydbooks` into that profile's `plugins/kaydbooks`, preserving
other plugins. Enable `kaydbooks` alongside its existing `plugins.enabled` entries
and add the MCP allowlist to that profile's config. Installing only in the default
profile does not configure a separate named-profile gateway.
Do not modify Hermes core, replace the global home chat or re-pair WhatsApp.

Keep `~/.hermes/kaydbooks/channel.json` private (mode 0600). Include `company`,
`chat_id`, `sender_ids`, the same `signing_secret`, `allow_outbound:false`, and a
fixed `ssh_command` array. It should invoke SSH with the administrator-configured
host alias and the Windows channel launcher, using strict host-key verification,
batch mode and a dedicated identity. No request fields are interpolated into shell code.

Set `KAYDBOOKS_HERMES_CONFIG` explicitly when using a named profile or a shared
private channel configuration. The existing MCP entry uses the separate **tools** launcher, not the channel launcher.
Limit its tools to `company_catalog_v1`, `capture_document_v1`, `extract_document_v1`,
`table_intake_v1`, `qbwc_entry_v1`, `batch_preview_v1`, `batch_status_v1`. Disable
parallel calls and MCP resources/prompts. Raw server discovery lists 42 tools;
the configured agent allowlist is only seven. A filter is not an OS security boundary.

After checking the installed Hermes plugin API, stop the worker while idle, update
the deployment, and restart the actual profile gateway normally. Verify WhatsApp
health before starting the worker; do not restart it during delivery. Test without sending:

```sh
cd ~/.hermes/plugins
/path/to/hermes/venv/bin/python -m kaydbooks.worker --once
```

Use that module without `--once` in a supervised service for 15-second polling.
Keep outbound disabled on both hosts until the exact destination and message test
are authorized. On Windows, populate `outbound_batch_ids` with only the explicitly
authorized batches before enabling delivery. Keep posting paused until the reviewed sample qualification is ready.
Requests must arrive within the Windows signed-request window. A short server-clock
cache handles ordinary host clock differences without changing either OS clock.

## Trust boundary and live acceptance still required

The gateway host administrator and fixed launcher are trusted. A model with
unrestricted root shell access on the gateway can access local secrets; prompt
instructions and MCP filtering cannot provide isolation from root. For stronger
separation, remove unrestricted execution from the operator agent and run signing
and approval components under a separately administered service identity.

The real operator still needs to exercise the actual attachment-to-batch conversation.
Exact reply, QBWC invoice readback and WhatsApp result delivery passed for the
controlled-source sample invoice. Regression tests check the wrong sender/company,
duplicate replies, stale evidence and lost delivery without any duplicate entry.
Multi-company installation and production authorization remain separate gates.
