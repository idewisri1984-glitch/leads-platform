# Bali Leads Platform

AI-assisted B2B lead acquisition and outreach workflow for discovering companies,
reviewing contacts, preparing email drafts, and recording delivery outcomes.

The application is under active development. Its CLI includes operations that can
access external providers, persist CRM data, and send email. Review command help and
confirmation prompts before using it with operational data.

## Requirements

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- SQLite for the default local database

## Local setup

Install the locked dependencies:

```powershell
uv sync --frozen
```

Create a local environment file from `.env.example`, then set only the credentials
needed for the operation you intend to run. `.env` is ignored by Git and must never
be committed.

The default database URL is:

```text
sqlite:///data/bali_leads.db
```

Initialize or upgrade the configured database before using persistence commands:

```powershell
$env:DEBUG = "false"
uv run alembic upgrade head
uv run alembic current
```

## Configuration

The complete supported environment-variable template is in `.env.example`.

- `DATABASE_URL` selects the SQLAlchemy database.
- `SERPAPI_*` settings enable company and contact search operations.
- `OPENAI_*` settings enable structured decisions and email-draft generation.
- `SMTP_*` settings enable the explicitly confirmed email-delivery workflow.
- `DEBUG=false` is the recommended operational default.

Blank provider credentials keep those provider-backed operations unavailable. Do not
place credentials in command output, logs, source files, or committed configuration.

## CLI

Inspect the root command and a command group before running an operation:

```powershell
uv run python -m app.cli.main --help
uv run python -m app.cli.main agent --help
uv run python -m app.cli.main crm --help
```

Read-only CRM examples:

```powershell
uv run python -m app.cli.main crm list
uv run python -m app.cli.main crm export-excel --help
```

Persistence and delivery commands intentionally require explicit confirmation where
the product contract demands it. Dry-run output is not proof that a mutation occurred.
Never bypass confirmation, duplicate protection, recipient validation, or stale-data
checks in automation.

## Validation

Run the same core checks used by CI:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -p no:cacheprovider
```

Validate migrations against an isolated database rather than an operational database:

```powershell
$env:DATABASE_URL = "sqlite:///data/validation.sqlite3"
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run alembic heads
```

## Operational safety

- Back up an operational SQLite database with the SQLite Backup API before migrations
  or controlled persistence runs.
- Keep discovery, AI generation, SMTP delivery, and manual-send recording as distinct
  operator-approved actions.
- Do not guess contact email addresses or create synthetic contacts.
- Do not treat a generated or approved draft as a sent email.
- Use an isolated `DATABASE_URL` for tests and migration validation.
- Keep exported workbooks and database backups outside version control.

## Technology stack

- SQLAlchemy and Alembic
- Pydantic and pydantic-settings
- Typer
- HTTPX
- OpenAI SDK
- openpyxl
- Pytest, Ruff, and mypy

## License

Proprietary.
