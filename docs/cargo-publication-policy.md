# Cargo publication policy

The [#128](https://github.com/acgetchell/research-repo-tools/issues/128) evaluation
selects **native Cargo publication** for the current shared capability. A custom
exact-archive registry uploader is a no-go for this release. The evidence core
supports retained-archive verification and reproduction without claiming that
native publication uploads those retained bytes.

The supported [Cargo publish interface](https://doc.rust-lang.org/cargo/commands/cargo-publish.html)
creates an archive from the checkout before uploading. Its `--no-verify` option
skips build verification; it does not accept an existing archive or suppress
packaging. A checked reproduction can establish equal bytes before approval or
token acquisition, but Cargo still packages again during actual publication.

The [registry web API](https://doc.rust-lang.org/cargo/reference/registry-web-api.html#publish)
accepts separate publication metadata and archive bytes, so an exact-byte
uploader is technically feasible. Maintaining it would add responsibility for
metadata/archive agreement, Cargo's publication restrictions, renamed and
target-specific dependencies, features and registry mappings, request framing,
short-lived credentials, limits, errors, uncertain upload outcomes and registry
checksum verification. Archive metadata inspection alone does not preserve all
of Cargo's publication checks.

The bounded alternative review included the upstream command and registry
interfaces, [cargo-release's publication configuration](https://github.com/crate-ci/cargo-release/blob/main/docs/reference.md),
and [release-plz's release operation](https://release-plz.dev/docs/usage/release).
Both orchestration tools document using Cargo publication. This review did not
establish a supported existing-archive path with equivalent metadata and
publication guarantees; it does not claim an exhaustive survey of all uploaders.

| Option | Benefit | Maintenance and guarantee boundary |
| --- | --- | --- |
| Native Cargo with signed source validation | Retains Cargo checks and established publishing workflow | Signed source validation does not attest archive bytes |
| Native Cargo with retained-archive checks and reproduction | Verifies original CI archive identity and equal local reproduction before credentials | Final native upload still repackages |
| Separate exact-archive uploader | Could keep consumer packaging outside the credential-bearing step and upload the retained bytes | Requires a separately reviewed, supported metadata/upload contract and checksum recovery |

Packaging, download and verification costs are **unmeasured**. There is no
performance claim or assumed savings. The potential provenance benefit does not
justify taking ownership of an additional publishing client within this bounded
release. Consumer native/scientific/feature checks, approval, registry absence,
tag/version/commit checks and post-upload visibility remain required.

A future proposal may reopen the uploader decision with a maintained supported
implementation or a bounded implementation design. It must preserve Cargo's
checks, bind exact archive and publication-metadata digests through validation
and approval, upload with short-lived credentials without executing consumer
code, and confirm the registry checksum. On an upload timeout, inspect registry
state and checksum before deciding recovery; never retry blindly. A speed claim
would additionally require representative packaging/download/verification
measurements. No existing consumer release depends on that future proposal.
