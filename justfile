set positional-arguments

default:
    @just --list

sync:
    uv sync --locked

check: workflow-check
    uv lock --check
    uv run --locked ruff check src scripts tests
    uv run --locked ruff format --check src scripts tests
    uv run --locked ty check src

workflow-check:
    uv run --locked actionlint
    uv run --locked zizmor --offline --strict-collection .github

# Network-backed advisory checks are separate from routine local validation.
audit:
    uv run --locked --group audit python scripts/audit_dependencies.py

test:
    uv run --locked pytest

build:
    uv lock --check
    uv build

install-check: build
    uv run --locked python scripts/check_install.py

ci: check test install-check
