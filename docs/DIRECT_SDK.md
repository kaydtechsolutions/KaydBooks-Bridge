# Durable direct SDK discovery

The direct SDK adapter supports only a fixed US qbXML 17.0 HostQuery/CompanyQuery
batch. It uses the same private configuration, principal and connector authentication,
company permissions, SQLite audit chain, and response/binding verifier as QBWC.
It has a separate company-scoped discovery journal; results are never represented
as QBWC callbacks or transaction-job completion. Live posting remains disabled.

## Prerequisites and invocation

Use Windows with the installed Intuit QBXMLRP2 runtime and interop assembly,
Windows PowerShell/.NET Framework, and an operator-confirmed synthetic company
open in single-user mode. The development SDK package is not required when these
runtime components are already installed. Only qbXML 17.0 has been qualified here;
the runtime must advertise that version before a query is sent.

Review the company claims privately and configure the expected connector digest
before running. An all-zero unconfirmed binding is blocked. File paths and names
alone do not establish identity. Keep configuration, credentials, database and
response evidence outside Git in an operator-restricted directory.

Run from the installed environment (example paths and identifiers are synthetic):

```powershell
python -m kaydbooks_bridge.direct_sdk `
  --config C:\BridgePrivate\bridge-config.json `
  --credentials C:\BridgePrivate\credentials.json `
  --principal test-operator --connector connector-company-a --run-id 1001
```

The secret file uses the existing deployment credential format. The command reads
the configured principal and connector secrets locally; it accepts no password
arguments. The principal needs `read` on the connector's configured company.
The authorized application retains the existing diagnostic application name so
that an installed grant can be reused. Grant access only while the sample company
is open, excluding personal data. The adapter both requests and checks SDK read-only
authorization; its XML allowlist independently excludes accounting writes.

## Durability and recovery

Each numeric run ID belongs to one actor and connector within one company database.
The immutable request is committed before dispatch. The native helper durably saves
the response privately, closes its session, and atomically publishes a response file.
Python persists that response before the shared validation step and audit event.
Repeating a completed run verifies the stored response without contacting QuickBooks.

Prepared/dispatched SDK runs exclude QBWC authentication for that company. Active
QBWC sessions exclude SDK discovery. An OS company lock serializes local workers;
a native global mutex prevents overlapping helper calls even if the Python parent
exits while its child remains alive. Different-company journals are separate, but
native calls on a Windows host are serialized because they use the open company.

After a restart, use the same run ID. A published response is recovered without
another request. Missing responses remain held: an operator with `recover` permission
may explicitly add `--recover-read` to issue the fixed read again. Check private
`sdk-exchange-*` evidence first. A response saved before failed session closure is
retained as evidence but is not automatically published. There is no write replay
path, automatic retry, expiry-based release, or delete/abandon command. Binding or
response mismatches terminate the run as blocked; investigate before using a new ID.

## Evidence and limits

Actual sample-company discovery, persisted binding after service restart, duplicate
execution from a new Python process without dispatch, and audit verification passed.
Crash windows, missing-response recovery, permission denial, mismatch rejection and
transport overlap are synthetic tests. A native process crash/power-loss qualification
has not been performed. Original private CompanyRet evidence is never committed.

QBWC read-only registration remains blocked by its AppLock metadata operation. Direct
SDK success does not qualify QBWC, Hermes, production companies, transaction adapters,
or broad report support. Protocol background is linked in [QBWC discovery](QBWC_DISCOVERY.md).
