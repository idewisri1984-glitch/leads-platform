# Architecture Decision Records

Version: 1.0

---

# ADR-001

## Title

Project is the primary business entity.

## Status

Accepted

## Context

The platform must support multiple businesses, industries, countries and lead generation campaigns simultaneously.

Managing data directly around Company would make multi-project management difficult.

## Decision

Project becomes the primary business entity.

Everything belongs to a Project.

Examples:

Project

↓

Workflows

↓

Companies

↓

Contacts

↓

Campaigns

↓

Reports

↓

Analytics

## Consequences

Benefits:

- Multi-project support
- Better scalability
- Easier permissions
- Better reporting
- Clear ownership of data

---

# ADR-002

## Title

Workflow Engine is the execution core of the platform.

## Status

Accepted

## Context

Lead generation is not a single action.

It is a sequence of independent business steps.

Search

↓

Analysis

↓

Qualification

↓

Contact Discovery

↓

Enrichment

↓

Campaign

↓

CRM

↓

Reports

Building separate AI agents for every operation would create unnecessary coupling.

## Decision

The platform will execute business processes using a Workflow Engine.

Every operation is represented as an independent Workflow Step.

Workflow Steps communicate only through defined inputs and outputs.

## Consequences

Benefits:

- Modular workflows
- Easy extension
- Reusable steps
- Better testing
- Easier debugging
- Parallel execution in the future

---

# ADR-003

## Title

Clean Architecture

## Status

Accepted

## Decision

The platform follows Clean Architecture.

Dependencies always point inward.

Presentation

↓

Application

↓

Domain

↓

Infrastructure

Infrastructure never contains business logic.

Repositories never contain AI logic.

AI services never contain SQL.

Database never defines business rules.

## Consequences

The platform remains modular and maintainable as it grows.

---

# ADR-004

## Title

Professional Technology Stack

## Status

Accepted

## Decision

The platform uses:

- Python 3.14
- SQLAlchemy 2.x
- Alembic
- Pydantic v2
- httpx
- OpenAI SDK
- Loguru
- Typer
- Pytest
- Ruff
- Black
- mypy

No custom ORM.

No custom migration framework.

## Consequences

The platform relies on proven technologies and minimizes maintenance cost.