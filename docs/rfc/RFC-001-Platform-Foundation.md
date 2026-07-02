# RFC-001 — Platform Foundation

Status: Accepted

Version: 1.0

Author: Bali Leads Platform Team

---

# Purpose

This RFC defines the engineering foundation of Bali Leads Platform.

The goal is to make all future development predictable, maintainable and scalable.

This document defines technologies, architecture principles and development workflow.

---

# Problem

The previous implementation evolved without a fixed architectural foundation.

As a result:

- infrastructure changed multiple times
- database architecture evolved repeatedly
- migration strategy changed
- repositories changed
- application structure changed

The experience was valuable.

This project starts from a clean architecture based on those lessons.

---

# Goals

The platform must be:

- modular
- testable
- scalable
- observable
- maintainable
- production-ready

---

# Technology Stack

Language

- Python 3.13

Package Management

- uv

Configuration

- pyproject.toml

Database

- SQLAlchemy 2.x

Database Migrations

- Alembic

Validation

- Pydantic v2

Settings

- Pydantic Settings

HTTP Client

- httpx

AI

- OpenAI SDK

Logging

- Loguru

Testing

- Pytest

Static Analysis

- Ruff

Formatter

- Ruff Formatter

Type Checking

- mypy

Git Hooks

- pre-commit

---

# Architecture

The platform follows Modular Clean Architecture.

Each business module owns its own:

- domain
- application
- infrastructure
- schemas
- tests

Modules communicate only through defined interfaces.

---

# Core Principles

Everything belongs to a Project.

Everything happens inside a Workflow.

Every Workflow consists of Steps.

Every Step requires a Capability.

Every Capability can have multiple Providers.

Infrastructure never contains business logic.

Repositories only persist data.

Services orchestrate business rules.

---

# Project Structure

The project follows this high-level structure.

app/

core/

modules/

providers/

presentation/

database/

tests/

docs/

scripts/

---

# Development Workflow

Every feature follows the same lifecycle.

RFC

↓

Architecture Review

↓

Implementation

↓

Tests

↓

Code Review

↓

Documentation

↓

Merge

---

# Definition of Done

A feature is complete only when:

- implementation is finished
- automated tests pass
- ruff passes
- mypy passes
- documentation updated
- review completed

---

# Non Goals

This project does not optimize for:

- shortest code
- experimental architecture
- premature optimization

The priority is maintainability.

---

# Decision

This RFC becomes the engineering foundation of Bali Leads Platform.

All future implementation must follow this document.

Changes require a