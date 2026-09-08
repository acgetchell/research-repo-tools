set positional-arguments

default:
    @just --list

sync:
    uv sync --locked --all-extras

check:
    uv lock --check
    uv run --locked ruff check src scripts tests
    uv run --locked ruff format --check src scripts tests
    uv run --locked ty check src

test:
    uv run --locked --all-extras pytest

build:
    uv lock --check
    uv build

install-check: build
    uv run --locked python scripts/check_install.py

ci: check test install-check
