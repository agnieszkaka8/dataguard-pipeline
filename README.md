# DataGuard

A typed Python 3.12 CLI that syncs records between two relational databases — validating each record against a JSON rules file, writing only valid records to the target, and logging every rejection to Supabase with the exact failure reason.

## Features

- **Validation engine** — 8 rule types: `required`, `type`, `gte`, `lte`, `gt`, `lt`, `eq`, `ne`, `regex`
- **Write-order guarantee** — rejections are written to Supabase *before* valid records are committed to Target DB; if the Supabase write fails, the entire run aborts
- **Incremental sync** — watermark-based extraction; only records newer than the last successful run are processed
- **Auth** — per-engineer Supabase Auth login; session cached locally between runs
- **Rule set management** — create, list, update, and delete named rule sets stored in Supabase instead of managing local JSON files
- **Dry-run mode** — validate without writing anything to Target DB

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Two PostgreSQL databases (source and target)
- A [Supabase](https://supabase.com) project

## Setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/agnieszkaka8/dataguard-pipeline.git
cd dataguard-pipeline
uv sync

# 2. Configure environment
cp .env.example .env
# Fill in SOURCE_DB, TARGET_DB, SUPABASE_URL, SUPABASE_KEY

# 3. Apply Supabase schema (run in Supabase SQL Editor)
# supabase/rejections.sql
# supabase/rule_sets.sql
# Then grant permissions:
# GRANT ALL ON public.rule_sets TO service_role;
# GRANT ALL ON public.rejections TO service_role;

# 4. Create your Supabase Auth user in the Supabase dashboard
# Authentication → Users → Add user
```

## Usage

```bash
# Login (required before any command)
uv run dataguard sync --table orders --rules rules/orders.json

# Dry-run (validate without writing)
uv run dataguard sync --table orders --rules rules/orders.json --dry-run

# Use a named rule set stored in Supabase
uv run dataguard sync --table orders --rule-set orders-v1

# Override watermark (re-process from a specific timestamp)
uv run dataguard sync --table orders --rules rules/orders.json --since 2026-01-01T00:00:00

# Logout
uv run dataguard logout
```

### Rule set management (CRUD)

```bash
# Create a rule set from a local JSON file
uv run dataguard rules create orders-v1 --table orders --from-file rules/orders.json

# List all rule sets
uv run dataguard rules list

# View a rule set's contents
uv run dataguard rules get orders-v1

# Update a rule set
uv run dataguard rules update orders-v1 --from-file rules/orders_v2.json

# Delete a rule set
uv run dataguard rules delete orders-v1
```

### Rules file format

```json
{
  "rules": [
    { "field": "email", "check": "required" },
    { "field": "email", "check": "type", "value": "str" },
    { "field": "email", "check": "regex", "value": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$" },
    { "field": "amount", "check": "gte", "value": 0 }
  ]
}
```

## Development

```bash
# Run tests
uv run pytest

# Type checking
uv run mypy dataguard/

# Lint + format
uv run ruff format . && uv run ruff check --fix .
```

## Stack

- [Typer](https://typer.tiangolo.com/) — CLI framework
- [Rich](https://rich.readthedocs.io/) — console output
- [psycopg2-binary](https://www.psycopg.org/) — PostgreSQL connections
- [supabase-py](https://github.com/supabase/supabase-py) — Supabase client
- [uv](https://docs.astral.sh/uv/) — package manager
