# Shared capability migration

These capabilities target v0.1.7, the next planned package release. Consumers adopt
an exact registry pin after publication and run their complete native gate;
they do not depend on sibling checkouts. Release history belongs in
[CHANGELOG.md](../CHANGELOG.md).

| Consumer implementation | Shared replacement | Retained consumer responsibility |
| --- | --- | --- |
| Repeated Python baseline literals and migration scripts | Installed `python_baseline`, `python_adoption`, and `toolchain adopt` | Opt-in declaration, exact package pin, public runtime support, lint policy and exceptions |
| Cargo deny installer/version wrapper | `toolchain.cargo.cargo-deny`, setup/check/run/export/upgrade | `deny.toml`, accepted advisories/licenses and scan scope |
| First-release special cases and fake predecessor tags | `release update --first-release`, validated release plans | Canonical target, release date, offline intent and publication |
| Notebook dependency installation checker | `notebooks.prohibit-installs`, blocking notebook lint | Opt-in policy, dependencies in locked groups, unsupported dynamic-call review |
| `tooling/security_tools.py`, `tooling/security-tools.toml` | Exact `toolchain.binaries`, verified release installation | Selected versions and consumer setup/cache wrappers |
| `tooling/security_scan.py` | `security osv`, `security secrets`, shared portable inventory | Lockfile selection, allowlists, schedules, workflow permissions and report upload |
| Generic inventory/report and Rust-doc parts of `tooling/semgrep.py` | `semgrep scan --rust-docs`, `semgrep check-fixtures --rust-docs` | Rules, scientific/model-loading restrictions, annotated fixtures and selected paths |
| Old package-managed tool installation cleanup | `toolchain clean` through the packaged `just clean` recipe | Retention roots for other consumers sharing the store |

Move representative shared contracts upstream: checksum/asset/version failures,
archive rejection, stale reports, status preservation, redaction, shallow history,
uncommitted-file coverage, Python migration rollback, first-release preparation,
and notebook/static-analysis source preservation. Delete duplicate generic tests
only once focused integration tests pass with the exact installed release.
Keep scientific fixtures and consumer-specific assertions in the consumer.

La-stack's former `clean` recipe removes build and coverage artifacts. Keep that
repository-specific work in a separate recipe when adopting the shared `clean`
contract. Shared cleanup handles obsolete owned installations. New package-managed
Python installs use the private store; existing user-wide uv interpreters remain
user-owned. See the [installation guide](INSTALLING.md#cleaning-obsolete-installations).

The former independent Python target mirrors are superseded only for opted-in
consumers. Explicit lower lint/type targets remain consumer policy. Unreleased-only
changelogs are accepted only during explicit first-release preparation; final
release validation remains strict. Native scanner schemas and Semgrep assertion
semantics remain upstream-owned rather than becoming consumer-specific variants.

Review adoption on Linux, macOS and Windows. Local deterministic models supplement
native installed-package checks; a successful host-only check does not establish
another platform's support. See [README](../README.md) for operational commands
and [CONTRIBUTING](../CONTRIBUTING.md) for package development.
