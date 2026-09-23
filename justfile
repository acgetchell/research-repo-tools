set positional-arguments

# Network-backed advisory checks are separate from routine local validation.
audit:
    uv run --locked --group audit python scripts/audit_dependencies.py

# Replace stale build artifacts with the current wheel and source distribution.
build:
    uv lock --check
    uv build --no-sources --clear

# Generate, normalize, and rotate completed minor series into docs/archives/changelog.
changelog:
    uv run --locked research-repo-tools changelog generate

# Archive completed minor series without regenerating Git history.
changelog-archive:
    uv run --locked research-repo-tools changelog archive

# Validate the entire changelog and all archives without writing files.
changelog-check:
    uv run --locked research-repo-tools changelog check

# Generate, normalize, and rotate completed minor series without publishing files.
changelog-preview *args:
    uv run --locked research-repo-tools changelog generate --dry-run "$@"

# Generate and archive a prospective release with an explicit YYYY-MM-DD date.
changelog-release tag date:
    uv run --locked research-repo-tools changelog generate --tag "$1" --date "$2"

alias changelog-unreleased := changelog-release

# Check the lockfile, Python linting, formatting, newlines, types, and workflows.
check: newline-check workflow-check
    uv lock --check
    uv run --locked research-repo-tools files run --include '*.py' --include '*.pyi' -- ruff check --no-fix --no-force-exclude
    uv run --locked research-repo-tools files run --include '*.py' --include '*.pyi' -- ruff format --check --no-force-exclude
    uv run --locked research-repo-tools files run --include '*.py' --include '*.pyi' -- ty check --no-force-exclude

# Validate existing artifacts without rebuilding them (also used by native CI).
check-dist:
    uv run --locked python scripts/check_install.py

# Install real tools and verify setup on disposable GitHub-hosted runners only.
check-setup:
    uv run --locked python scripts/check_setup.py

# Run checks, tests, builds, and isolated installation checks.
ci: check coverage install-check

# Run tests once with branch/subprocess coverage and write a Cobertura report.
coverage:
    uv run --locked pytest --cov --cov-report=term-missing --cov-report=xml:coverage/cobertura.xml

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

# Reject implicit platform-dependent newlines in Python text-file writes.
newline-check:
    uv run --locked python scripts/check_newlines.py

# Read-only publication preflight; does not create or push a tag.
release-check tag:
    uv run --locked python scripts/check_release.py "$1"

# Print release notes from the root changelog or an archive.
release-notes tag:
    uv run --locked research-repo-tools changelog notes "$1"

# Update release metadata using an explicit previous tag and UTC release date.
release-update version previous date:
    uv run --locked research-repo-tools release update "$1" --previous-release "$2" --date "$3"

# Review branch and local changes with CodeRabbit against a verified origin/main by default.
review base="origin/main":
    uv run --locked research-repo-tools review branch --base="$1"

# Review only staged, unstaged, and non-ignored untracked changes with CodeRabbit.
review-uncommitted:
    uv run --locked research-repo-tools review uncommitted

# Install user Just and synchronize the declared development environment.
setup:
    uv run --locked research-repo-tools setup

# Synchronize the locked development environment.
sync:
    uv sync --locked

# Create a local annotated tag using tag-release.
tag tag: (tag-release tag)

# Explicitly replace an existing local annotated tag.
tag-force tag:
    uv run --locked research-repo-tools changelog tag "$1" --force

# Preview the annotated tag without changing Git state.
tag-preview tag:
    uv run --locked research-repo-tools changelog tag "$1" --dry-run

# Create a local annotated tag from validated release notes.
tag-release tag:
    uv run --locked research-repo-tools changelog tag "$1"

# Run the Python test suite.
test:
    uv run --locked pytest

# Upgrade tools, then Python dependencies and the development environment.
update: update-tools update-dependencies

# Update Python dependencies without upgrading uv.
update-dependencies: update-python-dependencies

# Update direct dev pins, upgrade the full Python lock, and synchronize dev.
update-python-dependencies:
    uv run --locked research-repo-tools deps update-python
    uv lock --upgrade
    uv sync --locked --group dev

# Upgrade uv, then install the declared Just and Python environment.
update-tools: update-uv setup

# Upgrade uv through its installation owner and reconcile the project pin.
update-uv:
    uv run --no-config --no-sync --no-python-downloads research-repo-tools deps update-uv

# Run actionlint and zizmor with authenticated online audits when available.
workflow-check:
    uv run --locked actionlint
    uv run --locked research-repo-tools zizmor check
