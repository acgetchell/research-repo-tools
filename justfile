set positional-arguments

git_cliff_version := "2.14.2"

# Network-backed advisory checks are separate from routine local validation.
audit:
    uv run --locked --group audit python scripts/audit_dependencies.py

# Replace stale build artifacts with the current wheel and source distribution.
build: lock-check
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

# Install the pinned external generator through Cargo.
changelog-setup:
    cargo install git-cliff --version {{ git_cliff_version }} --locked

# Exercise generator contracts with the exact declared external version.
changelog-test: changelog-tools-check
    uv run --locked pytest tests/changelog/test_contract.py tests/changelog/test_cliff_template.py

# Require the declared generator rather than silently skipping integration tests.
[private]
changelog-tools-check:
    git-cliff --version
    test "$(git-cliff --version)" = "git-cliff {{ git_cliff_version }}"

# Check the lockfile, Justfiles, Python policies, and workflows.
check: lock-check justfile-check newline-check python-check workflow-check

# Validate existing artifacts without rebuilding them (also used by native CI).
check-dist:
    uv run --locked python scripts/check_install.py

# Exercise only real changelog generation from existing wheel/sdist installations.
check-dist-changelog: changelog-tools-check
    uv run --locked python scripts/check_install.py --changelog-only

# Install real tools and verify setup on disposable GitHub-hosted runners only.
check-setup:
    uv run --locked --no-sync --no-python-downloads python scripts/check_setup.py

# Run checks, tests, builds, and isolated installation checks.
ci: check coverage install-check

# Preview obsolete package-owned tool installs; pass --apply to remove them.
clean *args:
    uv run --locked --no-sync --no-python-downloads research-repo-tools toolchain clean "$@"

# Run tests once with branch/subprocess coverage and write a Cobertura report.
coverage:
    uv run --locked pytest --cov --cov-report=term-missing --cov-report=xml:coverage/cobertura.xml

# Show command help when invoked without a recipe.
[default]
[private]
default: help

# List recipes and arguments in lexicographic order, with aliases inline.
help:
    @just --justfile {{ quote(justfile()) }} --alias-style right --list

alias help-workflows := help

# Build and check isolated wheel and source-distribution installations.
install-check: build check-dist

# Check the maintainer and packaged consumer recipes with the pinned Just formatter.
justfile-check:
    just --justfile {{ quote(justfile()) }} --fmt --check
    just --justfile src/research_repo_tools/templates/justfile --fmt --check

# Verify the manifest and lock agree without changing dependencies.
lock-check:
    uv lock --check

# Reject implicit platform-dependent newlines in Python text-file writes.
newline-check:
    uv run --locked python scripts/check_newlines.py

# Check lint, formatting, and types for the complete tracked/nonignored Python inventory.
python-check:
    uv run --locked --no-sync --no-python-downloads research-repo-tools python check

# Read-only publication preflight; does not create or push a tag.
release-check tag:
    uv run --locked research-repo-tools release check "$1"

# Prepare a first release after verifying empty stable published history.
release-first tag date:
    uv run --locked research-repo-tools release update "$1" --first-release --date "$2"

# Print release notes from the root changelog or an archive.
release-notes tag:
    uv run --locked research-repo-tools changelog notes "$1"

# Approve the reviewed draft GitHub Release and trigger registry publication.
release-publish tag:
    uv run --locked research-repo-tools release publish "$1" --approve

# Create a local annotated release tag from validated notes (maintainer only).
release-tag tag:
    uv run --locked research-repo-tools changelog tag "$1"

# Preview the release annotation without changing Git state.
release-tag-preview tag:
    uv run --locked research-repo-tools changelog tag "$1" --dry-run

# Update release metadata using an explicit previous tag and UTC release date.
release-update tag previous date:
    uv run --locked research-repo-tools release update "$1" --previous-release "$2" --date "$3"

# Require the stable GitHub Release/assets and exact published registry version.
release-verify tag *args:
    uv run --locked research-repo-tools release verify "$@"

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

# Upgrade uv without repeating shell/bootstrap configuration.
update-tools: update-uv

# Upgrade uv through its installation owner and reconcile the project pin.
update-uv *args:
    uv run --no-config --no-sync --no-python-downloads research-repo-tools deps update-uv "$@"

# Run actionlint and zizmor with authenticated online audits when available.
workflow-check:
    uv run --locked actionlint
    uv run --locked actionlint src/research_repo_tools/templates/publish-crates.yml src/research_repo_tools/templates/zizmor.yml
    uv run --locked research-repo-tools zizmor check .github src/research_repo_tools/templates/publish-crates.yml src/research_repo_tools/templates/zizmor.yml
