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
Two controlled native process-termination windows also passed against the sample:

- After response save, before session closure/publication: journal remained dispatched,
  automatic replay was refused, and explicit authorized read recovery succeeded.
  The audit recorded exactly two dispatches, with only the second marked recovery.
- After closure/publication, before Python ingestion: recovery verified the saved
  response without another SDK call. The audit recorded exactly one dispatch.

Both original responses matched the operator-confirmed binding. A fresh Python
process subsequently replayed both completed runs without dispatch and verified audit
integrity. Private helper copies inserted only a marker and pause at each checkpoint;
the harness terminated the exact helper PID. It did not terminate QuickBooks. Private
evidence includes helper hashes, checkpoint/closure markers and original responses.

Parent-only termination with a surviving helper also passed against the sample.
The helper paused after response save, retaining its session and mutex. Terminating
the Python parent did not release the native mutex: explicit recovery's second helper
failed before SDK dispatch. Automatic replay was refused. Once released, the survivor
closed and published; recovery and a separate CLI invocation verified saved evidence
without another query. Private native evidence showed exactly one actual SDK dispatch.
Audit dispatch-intent events include rejected helper attempts and are not a count of
actual requests received by QuickBooks.

QBWC authenticate returned busy when invoked locally against this held journal; this
does not qualify actual Web Connector callbacks. Power loss and termination inside
ProcessRequest remain unqualified. Permission denial and mismatched-company tests
remain synthetic. Original private CompanyRet evidence is never committed.

QBWC read-only registration remains blocked by its AppLock metadata operation. Direct
SDK success does not qualify QBWC, Hermes, production companies, transaction adapters,
or broad report support. Protocol background is linked in [QBWC discovery](QBWC_DISCOVERY.md).
