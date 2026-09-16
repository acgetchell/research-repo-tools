set positional-arguments

# Network-backed advisory checks are separate from routine local validation.
audit:
    uv run --locked --group audit python scripts/audit_dependencies.py

# Build the wheel and source distribution.
build:
    uv lock --check
    uv build --no-sources

# Generate, normalize, and rotate completed minor series into docs/archives/changelog.
changelog:
    uv run --locked research-repo-tools changelog generate

# Archive completed minor series without regenerating Git history.
changelog-archive:
    uv run --locked research-repo-tools changelog archive

# Generate, normalize, and rotate completed minor series without publishing files.
changelog-preview *args:
    uv run --locked research-repo-tools changelog generate --dry-run "$@"

# Generate and archive a prospective release with an explicit YYYY-MM-DD date.
changelog-release tag date:
    uv run --locked research-repo-tools changelog generate --tag "$1" --date "$2"

alias changelog-unreleased := changelog-release

# Check the lockfile, Python linting, formatting, types, and workflows.
check: workflow-check
    uv lock --check
    uv run --locked ruff check src scripts tests
    uv run --locked ruff format --check src scripts tests
    uv run --locked ty check src scripts tests

# Validate existing artifacts without rebuilding them (also used by native CI).
check-dist:
    uv run --locked python scripts/check_install.py

# Run checks, tests, builds, and isolated installation checks.
ci: check test install-check

# Show command help when invoked without a recipe.
[default]
[private]
default: help

# List available commands and arguments in lexicographic order.
help:
    @just --justfile {{quote(justfile())}} --alias-style separate --list

alias help-workflows := help

# Build and check isolated wheel and source-distribution installations.
install-check: build check-dist

# Read-only publication preflight; does not create or push a tag.
release-check tag:
    uv run --locked python scripts/check_release.py "$1"

# Print release notes from the root changelog or an archive.
release-notes tag:
    uv run --locked research-repo-tools changelog notes "$1"

# Synchronize the locked development environment.
sync:
    uv sync --locked

# Create a local annotated tag using tag-release.
tag tag: (tag-release tag)

# Explicitly replace an existing local annotated tag.
tag-force tag:
    uv run --locked research-repo-tools changelog tag "$1" --force

# Create a local annotated tag from validated release notes.
tag-release tag:
    uv run --locked research-repo-tools changelog tag "$1"

# Run the Python test suite.
test:
    uv run --locked pytest

# Update exact development pins, refresh locked dependencies, and sync.
update:
    uv run --locked research-repo-tools deps update-python
    uv lock --upgrade
    uv sync --locked

# Run actionlint and offline zizmor workflow checks.
workflow-check:
    uv run --locked actionlint
    uv run --locked zizmor --offline --strict-collection .github
