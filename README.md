# KaydBooks Bridge

A multi-company QuickBooks Desktop automation platform under development,
with broad, optional Hermes integration. **Production posting is disabled.**
Explicit private gates permit bounded invoice, bill, payment and customer-credit tests in an
operator-confirmed sample company; see [controlled sample posting](docs/SAMPLE_POSTING.md).

The application lives in `kaydbooks_bridge`. The inherited `qbwc_kit` SOAP,
qbXML and Web Connector library remains available, with its MIT attribution.
Its [upstream reference](docs-upstream-qbwc-kit.md) describes transport examples,
not the Bridge application or deployment readiness. Do not expose those examples
as Bridge endpoints: they do not enforce Bridge company permissions or durable jobs.

## What works now

The agreed [first-release scope and acceptance checklist](docs/FIRST_RELEASE_SCOPE.md)
covers the remaining transactions, inputs, posting modes and reports. M3–M6 must meet
their required gates before final M7 deployment qualification; tax functionality and
tax reports are excluded from this release by operator choice; the current sample
invoice path is only part of that release.

- Explicit company context and per-company private SQLite databases.
- Environment-backed credentials, explicit company assignment, full supported permissions
  for newly assigned setup users unless restricted, and separate transaction approval.
- Strict synthetic invoice and source validation with decimal amounts and master allowlists.
- Durable draft, validated, queued, in-flight, posted-unverified, verified, blocked,
  failed and unknown state vocabulary. `failed` is reserved; uncertain outcomes stay held.
- Durable QBWC callback tickets and a fixed read-only Host/Company discovery request,
  with configured CompanyRet fingerprints, session isolation and replay-safe recovery.
- Atomic duplicate checks, serialized company dispatch, append-only audit events,
  saved-record comparison, pause, crash recovery and reconciliation without retries.
- A local simulation CLI usable without Hermes or QuickBooks, plus SDK/QBWC master and
  receipt checks and separately gated native sample invoice, bill and payment adapters.

Non-tax service/inventory/mixed invoices and expense/service/inventory bills have native
sample evidence. [Customer receipts](docs/CUSTOMER_PAYMENTS.md) support explicit partial,
full and unapplied amounts with independent read-back and reconciliation without resend.
[Supplier payments](docs/SUPPLIER_PAYMENTS.md) support explicit partial/full bill settlement
with independent payable checks and a separate bounded sample gate.
[Customer service credits](docs/CUSTOMER_CREDITS.md) reference an original invoice,
check prior Bridge-linked credits and verify the saved unapplied amount and customer
balance effect. [Credit application](docs/CREDIT_APPLICATION.md) verifies both existing
transaction links and balances without creating a payment. Refunds and broader credit
variants remain unfinished.

SDK/QBWC company binding and master/receipt reads have real sample-company evidence.
The optional [Hermes MCP adapter](docs/HERMES_TOOLS.md) adds source capture, preparation
and narrow tools. [Local workflows](docs/LOCAL_WORKFLOWS.md) add bounded scheduling,
preferences, delegation, board views and historical receipt reports. Signed snapshots
and isolated restore drills support [sample deployment qualification](docs/DEPLOYMENT_QUALIFICATION.md).
Broader transactions, native financial reports, OCR quality, external collaboration
and GUI accounting workflows remain unqualified or unavailable.
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

For your own company, start with [private company setup](docs/COMPANY_SETUP.md).
The setup command generates independent credentials and an unbound configuration;
the offline check lists missing prerequisites without contacting QuickBooks.

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
