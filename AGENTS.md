# Working in this repository

## Shared-tooling scope

- Maintain one implementation and one consistent contract per common capability.
  Organize source and tests by capability, not by consumer repository.
- Standardize common conventions and defaults. Do not add compatibility flags
  merely to preserve differences between the original scripts.
- Keep scientific algorithms, benchmark case selections, domain notebooks,
  deployment workflows, and other repository-specific decisions in consumers.
- Use small representative fixtures for shared formats and failure cases. Do
  not retain copied repositories, their lockfiles, old script implementations,
  or tests of those old implementations as the shared package's test suite.
- Preserve applicable regression behavior in tests of the shared implementation.
  Document deliberately superseded policies in the relevant guides.
- Test and validate shared tooling here. Consumers retain Rust and domain checks
  plus focused integration checks for their pinned package and configuration.

## Documentation conventions

- Keep consumer installation, commands, and usage examples in README.md;
  keep coding and package-development guidance in CONTRIBUTING.md.
- Document uv as a prerequisite and setup that installs a user-level Just command, then
  use `just ...` for routine commands without environment activation. Keep uv invocation details inside recipes.
- Name documents containing operational commands with UPPERCASE gerunds,
  such as `INSTALLING.md` and `VALIDATING.md`. Use lowercase filenames for
  informational documents, such as `api.md` and `migration.md`.
- Preserve established root filenames such as README.md, AGENTS.md,
  SECURITY.md, and the generated CHANGELOG.md. Update links when renaming docs.
- Keep recipe definitions, CLI help, and command-reference lists lexicographically
  sorted. Expose generated command listings through `just help` and the default
  recipe. Multi-step workflow examples retain their required execution order.

## Git operations

- **Never run Git commands that mutate version-control state.** This includes
  staging, committing, pushing, tagging, switching branches, merging, rebasing,
  resetting, stashing, cleaning, and other index, ref, or checkout mutations.
- Use `git --no-pager` for read-only inspection, including status, diff, log,
  show, and blame.
- Suggest mutating Git commands for the user to run manually; do not execute
  them. Do not bypass this rule through other tools, APIs, or direct `.git` edits.
- Preserve user changes and do not revert unrelated work.

## Validation

- Use `just check` for routine validation while implementing changes.
- Reserve `just ci` for final validation when the implementation is ready for
  review. It runs the full test suite, builds the package, and checks isolated
  wheel and sdist installations.
- Do not repeatedly run the full test suite, builds, or installation checks
  during iteration. Run narrowly targeted tests only when needed to diagnose
  or verify a specific behavioral change.
- Record unfinished or failed checks accurately. A passing `just check` is
  not evidence that the full tests or package installation checks passed.
- Record validation results in PR descriptions, review notes, or CI logs.
  Keep documentation focused on current guidance. Link to the generated
  [CHANGELOG.md](CHANGELOG.md) for fixes and release history; do not maintain
  parallel fix or validation logs in other documents.

## Cross-platform changes and fixtures

- Review runtime code and tests for Linux, macOS, and Windows semantics when
  changing paths, archives, byte transport, subprocesses, or package installation.
  Account for separators, case-insensitive Path equality, encodings, newline
  translation, executable discovery, permissions, and symlink privileges.
- Verify fixture preconditions before testing behavior. For exact-byte files,
  use bytes or explicit encoding/newline settings. For malformed archives, assert
  the original stored member names: standard-library writers can normalize unsafe
  inputs into safe ones on Windows. Do not assume constructor arguments survive
  serialization unchanged.
- Give Python text-file writes an explicit newline policy, including fixture and
  child-script writers. `just check` runs `just newline-check` to reject implicit
  translation. Fix detected writers; preserve deliberate violation snippets used
  to test static-analysis coverage and their expected findings. Keep intentional
  LF/CRLF byte regressions intact.
- Add public API regressions to the consumer suites run from isolated wheel and
  sdist installations. Exercise deterministic platform transformations locally
  with focused models where useful; avoid changing global OS identity or skipping
  supported behavior to make a platform pass. Models supplement native checks.
- Diagnose native failures from the failing job's logs. Before reporting a change
  as ready to merge, verify the required Linux, macOS, and Windows package jobs
  for the current commit. If those runs are unavailable or pending, report the
  remaining native checks explicitly; a local `just ci` covers only its host.
