# KaydBooks Bridge

A multi-company QuickBooks Desktop automation platform under development,
with broad, optional Hermes integration. **This release is a simulation-only
foundation. It cannot post to QuickBooks.**

The application lives in `kaydbooks_bridge`. The inherited `qbwc_kit` SOAP,
qbXML and Web Connector library remains available, with its MIT attribution.
Its [upstream reference](docs-upstream-qbwc-kit.md) describes transport examples,
not the Bridge application or deployment readiness. Do not expose those examples
as Bridge endpoints: they do not enforce Bridge company permissions or durable jobs.

## What works now

- Explicit company context and per-company private SQLite databases.
- Environment-backed credentials, deny-by-default company permissions, separate approval.
- Strict synthetic invoice and source validation with decimal amounts and master allowlists.
- Durable draft, validated, queued, in-flight, posted-unverified, verified, blocked,
  failed and unknown state vocabulary. `failed` is reserved; uncertain outcomes stay held.
- Durable QBWC callback tickets and a fixed read-only Host/Company discovery request,
  with configured CompanyRet fingerprints, session isolation and replay-safe recovery.
- Atomic duplicate checks, serialized company dispatch, append-only audit events,
  saved-record comparison, pause, crash recovery and reconciliation without retries.
- A local CLI usable without Hermes or QuickBooks. Only a synthetic invoice adapter exists.

Native Hermes adapters, real SDK-qualified company binding, transaction/report support,
document extraction, schedules, notifications, memory and GUI workflows remain planned.
An interface label in a test envelope does not mean that Hermes interface is connected.

## Development

```powershell
uv sync --frozen --extra dev --python 3.12
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen kaydbooks-bridge capabilities
```

CI tests Python 3.10–3.13 on Windows and Linux. The distribution name is
`kaydbooks-bridge`; this repository does not publish the upstream `qbwc-kit` package.
The build workflow only creates review artifacts. Publishing is disabled.

## Try the foundation

Follow the [simulation quickstart](docs/OPERATIONS.md). Copy the synthetic config
template to a private directory **outside every Git checkout**, set its absolute
state directory, and supply temporary credentials through environment variables.
The checked-in template deliberately cannot run as deployment configuration.

```powershell
uv run --frozen kaydbooks-bridge --company company-a check-config
uv run --frozen kaydbooks-bridge --company company-a prepare examples/synthetic-invoice.json
```

Every operation requires a private `KAYDBOOKS_CONFIG` path and an authenticated
`KAYDBOOKS_TOKEN`, except the public `capabilities` inventory. Never put credentials
on the command line or in job payloads.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), the [architecture and durable plan](docs/ARCHITECTURE.md),
[capability matrix](docs/CAPABILITIES.md), [operations guide](docs/OPERATIONS.md), and
[durable QBWC discovery protocol](docs/QBWC_DISCOVERY.md), and
[M2 qualification runbook](docs/M2_QUALIFICATION.md), and
[expanded scope from PR #1](HERMES_INTEGRATION_SCOPE.md).

## License

MIT. Includes `qbwc-kit`, copyright 2026 Eren Altuntas; see [LICENSE](LICENSE).
