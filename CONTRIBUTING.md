# Contributing

Create a focused branch, preserve existing operator work and keep private data outside the
checkout. Add tests for material workflow, authorization and recovery behavior. Run:

```sh
uv sync --frozen --extra dev --extra hermes --extra remote
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
sh -n deploy/install.sh deploy/doctor.sh
uv build
```

Document observable behavior and qualification limits. A feature is not release-qualified
until its acceptance gate has retained evidence. Do not enable publishing in CI or commit
credentials, customer names, company-file paths, source documents or raw accounting data.
