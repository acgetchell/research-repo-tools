set positional-arguments

default:
    @just --list

sync:
    uv sync --locked

# Update exact development pins, refresh locked dependencies, and sync.
update:
    uv run --locked research-repo-tools deps update-python
    uv lock --upgrade
    uv sync --locked

check: workflow-check
    uv lock --check
    uv run --locked ruff check src scripts tests
    uv run --locked ruff format --check src scripts tests
    uv run --locked ty check src scripts tests

workflow-check:
    uv run --locked actionlint
    uv run --locked zizmor --offline --strict-collection .github

# Network-backed advisory checks are separate from routine local validation.
audit:
    uv run --locked --group audit python scripts/audit_dependencies.py

changelog-preview:
    uv run --locked research-repo-tools changelog generate --dry-run

changelog:
    uv run --locked research-repo-tools changelog generate

test:
    uv run --locked pytest

build:
    uv lock --check
    uv build --no-sources

# Validate existing artifacts without rebuilding them (also used by native CI).
check-dist:
    uv run --locked python scripts/check_install.py

install-check: build check-dist

# Read-only publication preflight; does not create or push a tag.
release-check tag:
    uv run --locked python scripts/check_release.py "$1"

ci: check test install-check
