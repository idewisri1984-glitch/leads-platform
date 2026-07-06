# Leads Platform — Project Context

## 1. Purpose of this document

This document provides persistent context for Codex and other AI coding agents working on the Leads Platform project.

Before making any code changes:

1. Read this entire file.
2. Inspect the actual repository structure and current code.
3. Read the relevant existing modules, tests, migrations, and configuration.
4. Treat the repository code as the source of truth.
5. Do not assume that this document is more current than the actual code.
6. Preserve the existing architecture and coding style unless a change is clearly necessary.
7. Run tests and quality checks after each logical development stage.

The project has been developed incrementally with ChatGPT. Previous work included architecture discussions, implementation, testing, debugging, Git commits, and feature branches.

The next major development task is the Contact module.

---

# 2. Project Overview

Project name:

Leads Platform

Repository:

idewisri1984-glitch/leads-platform

Primary purpose:

Build a modular CRM and lead-generation platform capable of:

- storing projects;
- storing companies;
- storing contacts;
- managing leads;
- managing outreach;
- collecting company information;
- collecting contact information;
- integrating Google Maps data collection;
- integrating LinkedIn-related workflows where technically and legally appropriate;
- parsing company websites;
- using AI to analyze companies;
- using AI to identify relevant decision-makers;
- generating personalized outreach messages;
- managing email campaigns;
- exposing functionality through CLI;
- later exposing functionality through FastAPI;
- later providing a dashboard;
- eventually running with PostgreSQL and Docker.

The system is intended to grow into a real business application rather than remain a tutorial CRUD project.

---

# 3. Development Philosophy

The project should be developed incrementally.

Preferred workflow:

1. Inspect current code.
2. Define one small logical task.
3. Implement the task.
4. Add or update tests.
5. Run tests.
6. Run Ruff and other configured quality checks.
7. Fix all failures.
8. Review the diff.
9. Commit.
10. Push.
11. Continue to the next logical task.

Avoid large speculative rewrites.

Avoid premature abstractions.

Do not introduce architecture merely because it might be useful in the future.

Follow YAGNI where appropriate.

Prefer simple, explicit, readable code over unnecessary abstraction.

Existing stable modules should not be rewritten without a concrete reason.

---

# 4. Important Working Rules

## 4.1 Repository is the source of truth

Always inspect the actual current files before editing.

Do not generate replacements based only on this context document.

The repository may have changed after this document was created.

Before implementing a feature:

- inspect related models;
- inspect repositories;
- inspect services;
- inspect schemas;
- inspect CLI implementation;
- inspect tests;
- inspect Alembic configuration;
- inspect existing migrations.

---

## 4.2 Preserve project consistency

New modules should follow the established style of existing modules.

In particular, inspect:

- Project module;
- Company module.

The Contact module should follow the successful patterns already established by these modules unless there is a clear technical reason not to.

---

## 4.3 Full-file preference when communicating code

The project owner prefers receiving complete file contents instead of partial snippets.

When explaining manual changes to the project owner:

- always provide the exact file path;
- provide the entire file when practical;
- clearly say whether the file should be created or replaced;
- avoid ambiguous instructions such as "add this somewhere";
- provide commands in the exact order they should be executed.

When Codex can edit files directly, it may make targeted edits, but it must summarize exactly which files were changed.

---

## 4.4 Do not change direction repeatedly

Before proposing an architectural change:

1. inspect the actual project;
2. identify a concrete current problem;
3. explain why the change is necessary now;
4. consider whether the existing architecture is sufficient.

Do not repeatedly change the roadmap based on speculative improvements.

The current priority is product development.

---

## 4.5 One logical stage at a time

Do not implement an entire large module in one uncontrolled change.

Preferred example for Contact:

1. Contact model and model registration.
2. Alembic migration.
3. Model/migration verification.
4. Contact repository.
5. Repository tests.
6. Contact schemas.
7. Contact service.
8. Service tests.
9. Contact CLI.
10. CLI verification/tests where appropriate.
11. Full test suite.
12. Ruff/pre-commit.
13. Review.
14. Commit and push.

---

# 5. Current Technology Stack

Inspect `pyproject.toml` and lock files for exact versions.

The project currently uses or has used:

- Python;
- uv;
- SQLAlchemy 2.x style ORM;
- Alembic;
- Pydantic;
- pydantic-settings;
- Typer;
- pytest;
- Ruff;
- pre-commit;
- SQLite during current development;
- Git;
- GitHub.

Future planned technologies may include:

- PostgreSQL;
- FastAPI;
- Docker;
- AI integrations;
- data collectors;
- dashboard technologies.

Do not add future technologies before they are needed.

---

# 6. Current Architecture

The repository uses a modular architecture.

General structure is approximately:

app/
    cli/
    core/
        config/
        database/
    modules/
        project/
        company/

tests/

alembic/

Exact current structure must be inspected before changes are made.

Modules generally follow a structure similar to:

app/modules/<module>/
    __init__.py
    models.py
    repository.py
    schemas.py
    service.py

CLI commands live under:

app/cli/

Database infrastructure lives under:

app/core/database/

Configuration lives under:

app/core/config/

---

# 7. Architecture Principles Already Used

The project currently uses several architectural patterns.

## ORM Model

SQLAlchemy ORM models represent persisted entities.

The project uses modern SQLAlchemy 2.x patterns, including concepts such as:

- DeclarativeBase;
- Mapped;
- mapped_column;
- relationship.

Inspect existing models for exact conventions.

---

## Repository Layer

Repositories handle database persistence and queries.

Existing examples include:

- ProjectRepository;
- CompanyRepository.

Repositories should focus on database operations.

Business logic should not be placed in repositories unless there is a strong reason.

---

## Service Layer

Services sit between interfaces such as CLI/API and repositories.

Existing examples include:

- ProjectService;
- CompanyService.

The Project service uses Pydantic schemas and converts ORM objects into read schemas.

The Company service was designed to follow the same style.

Inspect the current implementation before adding ContactService.

---

## Pydantic Schemas

Schemas are used for validated input and output.

Existing examples include concepts such as:

- ProjectCreate;
- ProjectRead;
- CompanyCreate;
- CompanyRead.

Contact should follow the current established schema style.

---

## CLI

The project uses Typer.

Existing CLI modules include Project and Company functionality.

Company CLI has included commands such as:

- create;
- list;
- show;
- delete.

Inspect the current CLI implementation and main command registration before adding Contact CLI.

---

# 8. Database Architecture

The project currently uses SQLite during development.

Database configuration and session management exist under:

app/core/database/

Files previously discussed include:

- base.py;
- engine.py;
- session.py.

Do not assume their exact current contents.

Inspect them before making database-related changes.

---

# 9. SQLAlchemy Model Registration

A previous issue occurred when Project had a relationship to Company but the Company model had not been imported before SQLAlchemy mapper configuration.

The error was similar to:

relationship("Company") failed to locate a name ("Company")

The project was updated so ORM models are registered correctly.

Before adding Contact:

1. inspect the current model registration mechanism;
2. determine how Project and Company are registered;
3. follow the same mechanism for Contact;
4. do not introduce circular imports;
5. run the full test suite after registration changes.

This is an important known area of the project.

---

# 10. Alembic

Alembic is installed and configured.

Existing migrations include Project and Company-related schema changes.

Previous workflow:

uv run alembic revision --autogenerate -m "<migration message>"

Then inspect the generated migration.

Then:

uv run alembic upgrade head

Then verify:

uv run alembic current

Important:

Never blindly trust an autogenerated migration.

Always inspect:

- upgrade();
- downgrade();
- foreign keys;
- indexes;
- nullable settings;
- defaults;
- enum handling;
- timestamp handling.

Do not rewrite old migrations simply to make the history look cleaner.

Add a new migration for new schema changes.

---

# 11. Existing Project Module

The Project module is a stable existing module.

It includes functionality across several layers.

Known concepts include:

Project model.

ProjectRepository with methods that have included:

- create;
- get;
- get_all;
- update;
- delete.

ProjectService.

ProjectCreate and ProjectRead schemas.

Project CLI.

Tests.

Inspect all current Project files before using them as templates.

Do not unnecessarily refactor Project while implementing Contact.

---

# 12. Existing Company Module

The Company module was developed on a feature branch and then completed.

Known Company fields have included:

- id;
- project_id;
- name;
- website;
- country;
- city;
- industry;
- status;
- notes.

Inspect the actual model for current types, constraints, defaults, relationships, and indexes.

Known CompanyRepository methods have included:

- create;
- get;
- get_all;
- get_by_project;
- update;
- delete.

Known schemas include:

- CompanyCreate;
- CompanyRead.

Known CompanyService methods have included:

- create;
- get;
- get_all;
- get_by_project;
- update;
- delete.

Known Company CLI commands include:

- create;
- list;
- show;
- delete.

The Company module has repository and service tests.

At one stage, the full suite reported 17 passing tests.

The exact current test count may differ.

Do not rely on 17 as an expected fixed count.

---

# 13. Known Previous Bugs and Lessons

## Settings regression

At one point, `app_name` disappeared from Settings and caused a failing test.

Lesson:

Do not replace configuration files without inspecting all existing settings and tests.

---

## ORM model registration

Company relationship resolution previously failed because the model had not been registered before mapper configuration.

Lesson:

Model import and registration must be handled deliberately.

---

## Import/export issue

Company CLI previously failed with:

ImportError: cannot import name 'CompanyCreate' from 'app.modules.company'

The issue involved exports from:

app/modules/company/__init__.py

Lesson:

When adding a new module:

- inspect package exports;
- ensure schemas, repositories, services, and models are imported only where appropriate;
- avoid accidental circular imports.

---

## Ruff formatting

At least one commit required Ruff to reformat files before the commit succeeded.

Lesson:

Run quality checks before considering a stage complete.

---

# 14. Testing Strategy

The project uses pytest.

Existing tests cover areas including:

- settings;
- database;
- Project repository;
- Project service;
- Company repository;
- Company service.

Before adding Contact, inspect all current tests.

New Contact development should include tests.

At minimum:

Contact repository tests should verify:

- create;
- get;
- get_all;
- get_by_company;
- update;
- delete.

Contact service tests should verify:

- creation from a schema;
- ORM-to-read-schema conversion;
- retrieval;
- list operations;
- company filtering;
- expected missing-record behavior where relevant.

Tests must not depend on test execution order.

Be cautious because the current tests may use the configured development SQLite database rather than isolated per-test databases.

Inspect the current test setup carefully before expanding the suite.

If test isolation is currently weak, identify it clearly.

Do not perform a major test infrastructure rewrite unless necessary for Contact implementation.

---

# 15. Git Workflow

The project uses Git and GitHub.

Previous workflow included feature branches such as:

feature/company

A typical development flow is:

main
    ↓
feature/<module>
    ↓
development
    ↓
tests
    ↓
commit
    ↓
push
    ↓
Pull Request
    ↓
merge to main

Before starting Contact:

1. inspect the current Git branch;
2. inspect git status;
3. ensure existing work is committed;
4. ensure main is current;
5. create a feature branch if one does not already exist.

Suggested branch:

feature/contact

Do not commit unrelated changes together.

Suggested logical commits:

feat(contact): add ORM model

feat(contact): add repository CRUD

feat(contact): add schemas and service

feat(contact): add CLI

Exact commit boundaries may change if the repository structure makes another split more appropriate.

---

# 16. Current Product Roadmap

Completed or substantially completed:

- project foundation;
- database infrastructure;
- SQLAlchemy;
- Alembic;
- Project module;
- Company module;
- repository pattern;
- service layer;
- Pydantic schemas;
- CLI;
- pytest;
- Ruff;
- pre-commit;
- GitHub workflow.

Next:

- Contact.

Then likely:

- Lead;
- Task;
- collectors;
- website parser;
- AI analysis;
- AI outreach;
- FastAPI;
- dashboard;
- PostgreSQL;
- Docker;
- production deployment.

The roadmap may evolve based on actual product needs.

---

# 17. Next Module: Contact

The next development priority is Contact.

Contact represents a person associated with a Company.

Primary relationship:

Project
    ↓
Company
    ↓
Contact

Future relationships may include:

Contact
    ↓
Lead
    ↓
Task / Outreach / Email / Meeting

Do not implement future entities during Contact development.

---

# 18. Proposed Contact Data Model

The previously discussed Contact design included:

- id;
- company_id;
- first_name;
- last_name;
- job_title;
- email;
- phone;
- linkedin_url;
- country;
- city;
- source;
- external_id;
- status;
- notes;
- created_at;
- updated_at.

This is a proposal, not an instruction to implement blindly.

Before implementing:

1. inspect current Company model conventions;
2. inspect schema conventions;
3. inspect migration conventions;
4. decide exact nullable behavior;
5. decide field lengths;
6. decide indexes;
7. decide whether timestamps should be introduced now;
8. decide whether status should use a Python Enum, SQLAlchemy Enum, string field, or existing project convention;
9. consider duplicate prevention requirements.

Keep the first Contact version useful but simple.

---

# 19. Contact Relationship

Expected relationship:

Company has many Contacts.

Contact belongs to one Company.

Conceptually:

Company.contacts

Contact.company

Before implementation:

- inspect Project ↔ Company relationship;
- follow the established relationship style;
- determine current cascade behavior;
- do not guess cascade semantics.

The foreign key should reference the actual Company table name from the current model.

---

# 20. Contact Status

Previously discussed possible statuses included:

- NEW;
- ACTIVE;
- CONTACTED;
- NO_RESPONSE;
- CLIENT;
- ARCHIVED.

Do not implement all statuses simply because they were discussed.

First determine the current business need.

A simpler initial status model may be preferable.

If an enum is implemented:

- use a clearly defined enum;
- ensure Pydantic compatibility;
- ensure SQLAlchemy compatibility;
- ensure Alembic migration behavior is understood;
- consider future PostgreSQL migration implications.

Avoid introducing a global enum infrastructure prematurely.

---

# 21. Contact Source

A `source` field was proposed because future collectors may create contacts.

Possible values:

- manual;
- linkedin;
- google_maps;
- website;
- import;
- referral;
- ai.

This field may be useful for data provenance.

However, do not over-engineer the initial implementation.

Inspect future collector plans and current conventions before choosing enum vs string.

---

# 22. External ID

An `external_id` field was proposed for future integrations.

Potential uses:

- prevent duplicate imported records;
- store external provider identifiers;
- correlate collector results.

Potential problem:

An external ID may only be unique together with its source.

If implemented, consider whether uniqueness should conceptually be:

(source, external_id)

rather than:

external_id

Do not add an incorrect global uniqueness constraint without considering this.

---

# 23. Timestamps

Timestamps were discussed for Contact:

- created_at;
- updated_at.

A TimestampMixin was also discussed and then intentionally postponed.

Current architectural decision:

Do not create generic mixins or infrastructure solely for future use.

If Contact needs timestamps now, implement them in the simplest way consistent with the project.

Do not retrofit Project and Company solely for architectural symmetry unless there is a concrete product requirement.

Do not introduce TimestampMixin automatically.

---

# 24. BaseRepository and BaseService Decision

Generic BaseRepository and BaseService abstractions were discussed.

Current decision:

Do not implement them yet.

Reason:

Only a small number of modules currently exist.

The actual duplication pattern is not yet large enough to justify generic infrastructure.

Continue with explicit repositories and services.

Reconsider shared abstractions only after several modules reveal stable repeated patterns.

---

# 25. Logging, Exceptions, and Constants

A production-hardening sprint involving:

- logging;
- custom exceptions;
- constants;
- enums;
- timestamp mixins

was discussed.

Current decision:

Do not stop product development for a broad infrastructure sprint.

Add these capabilities when a concrete module requires them.

Avoid speculative infrastructure work.

---

# 26. Contact Implementation Plan

Codex should first inspect the repository.

Then provide a concise implementation plan based on the actual code.

Recommended implementation stages:

## Stage 1 — Preparation

- inspect git status;
- inspect current branch;
- inspect Project model;
- inspect Company model;
- inspect model registration;
- inspect Alembic env;
- inspect current migrations;
- inspect repository conventions;
- inspect schema conventions;
- inspect service conventions;
- inspect CLI conventions;
- inspect tests.

Do not edit yet.

Report any current repository inconsistencies before implementation.

## Stage 2 — Contact ORM Model

Implement:

- Contact package if absent;
- Contact ORM model;
- Company relationship update;
- model registration update if required.

Add focused tests if appropriate.

Run:

uv run pytest

Run configured Ruff checks.

Review diff.

## Stage 3 — Migration

Generate:

uv run alembic revision --autogenerate -m "add contacts"

Inspect migration manually.

Then:

uv run alembic upgrade head

Verify:

uv run alembic current

Run tests again.

## Stage 4 — Repository

Implement ContactRepository following CompanyRepository style.

Likely methods:

- create;
- get;
- get_all;
- get_by_company;
- update;
- delete.

Add repository tests.

Run tests and Ruff.

## Stage 5 — Schemas and Service

Implement schemas following current Pydantic conventions.

Likely:

- ContactCreate;
- ContactRead.

Implement ContactService following the current service style.

Add service tests.

Run tests and Ruff.

## Stage 6 — CLI

Implement Contact CLI following Company CLI conventions.

Potential commands:

- create;
- list;
- show;
- delete.

Consider whether filtering by company should be included.

Register the CLI in the main Typer application.

Run tests and manual CLI checks.

## Stage 7 — Review

Before merging:

- run full pytest suite;
- run Ruff;
- run pre-commit if configured;
- inspect git diff;
- inspect migration;
- inspect Git status;
- confirm no generated junk files are tracked;
- confirm no secrets are tracked;
- confirm README changes if necessary.

Then commit/push and create PR.

---

# 27. Commands Commonly Used

Inspect project configuration before assuming all commands are valid.

Previously used commands include:

uv run pytest

uv run ruff check .

uv run ruff format .

uv run alembic revision --autogenerate -m "message"

uv run alembic upgrade head

uv run alembic current

uv run python -m app.cli.main

git status

git add .

git commit -m "message"

git push

---

# 28. Important Instruction for Codex

The first task after reading this document is NOT to immediately create Contact files.

First:

1. inspect the complete repository;
2. inspect Git status and branch;
3. inspect current architecture;
4. inspect all Project and Company module files;
5. inspect tests;
6. inspect migrations;
7. compare the repository against this document;
8. identify any differences or outdated assumptions.

Then report:

- current repository state;
- current architecture;
- current test status;
- current migration state;
- any problems that should be fixed before Contact;
- recommended exact Contact Stage 1 plan.

Do not make broad architecture changes.

Do not implement BaseRepository.

Do not implement BaseService.

Do not introduce speculative infrastructure.

Do not rewrite stable modules without a concrete reason.

After the review, proceed with Contact one logical stage at a time.

---

# 29. Initial Prompt to Use with Codex

After placing this file in the repository root, send Codex this prompt:

Read PROJECT_CONTEXT.md completely.

Then inspect the entire repository, including the current Git branch and status, pyproject.toml, app structure, Project module, Company module, CLI, database infrastructure, Alembic configuration and migrations, and all tests.

Treat the repository as the source of truth and PROJECT_CONTEXT.md as historical and planning context.

Do not modify any files yet.

Run the existing test suite and configured quality checks.

Then give me a concise code review containing:

1. current project state;
2. current architecture;
3. test and quality-check results;
4. migration state;
5. inconsistencies or technical debt that should be addressed before Contact;
6. the exact recommended implementation plan for Contact Stage 1.

Do not propose BaseRepository or BaseService.
Do not perform speculative refactoring.
Do not rewrite stable Project or Company modules without a concrete reason.

After I approve the plan, implement Contact one logical stage at a time.

When communicating manual code changes, always provide complete files and exact file paths.