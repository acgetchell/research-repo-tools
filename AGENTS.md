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
