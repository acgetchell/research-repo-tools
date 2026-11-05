# Stable Cargo documentation

`validation cargo-docs PACKAGE` and
`cargo_docs.check_documentation(root, package, *, features=(), default_features=True, all_features=False, cfg=(), target=None)`
build the public library docs for one Cargo workspace member. The root contains
the workspace manifest and `rust-toolchain.toml`, whose channel pins a stable
`X.Y.Z` release. Install the declared compiler through the consumer's toolchain
setup before running the check. The [shared Just recipe](RUNNING_CHECKS.md#cargo-documentation)
passes the consumer's selection through to the CLI.

Cargo packages the member with the workspace lock enforced. The check extracts
that exact crate archive, then runs stable rustdoc on its library with its
packaged lock and warnings denied. The archive, extracted sources, and build
outputs live in temporary storage. The source tree, manifests, and locks stay in
place. Cargo failures propagate to Python callers and give the CLI a nonzero
exit status with the diagnostic.

Defaults select the host target and Cargo's default features. Use repeatable
`--features NAME` with optional `--no-default-features`, or select
`--all-features` on crates that support the combination. An explicit
`--target TRIPLE` requires an installed target. Cargo reads the consumer's
normal configuration from the workspace root. Use repeatable `--cfg VALUE` for
an explicit stable-compatible rustdoc configuration. Cargo and rustdoc report
unsupported options and unstable crate features as errors. The check rejects
`RUSTC_BOOTSTRAP`. Enable a `docsrs` cfg only by explicit configuration when the
crate supports it on the selected stable compiler.

Success returns `DocumentationEvidence` in Python and emits a JSON object in the
CLI. It includes the package name and version, SHA-256 digests of the source
manifest, workspace lock and exact crate archive, the declared toolchain and
full compiler version, the target, feature selection, explicit cfg values, and
a digest of the Cargo configuration files and inherited compiler flags.

Keep private-item and workspace checks in the consumer's existing validation
gate. This check covers public library documentation on the pinned release
compiler and catches inputs missing from the published crate layout. The hosted
docs.rs build applies its own metadata, compiler and target configuration.
Newer compilers may report additional diagnostics, and the hosted build
determines the docs.rs badge.
