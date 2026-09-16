# Distribution status

This package has not been published to PyPI. There is no publishing workflow or
publisher draft. Do not upload artifacts or change account settings without a
new explicit user request.

PyPI is the intended distribution source for consuming repositories. Consumers
will pin the published package in their Python development dependencies and lock
it with uv. GitHub hosts source, issues, review, and CI; local build artifacts
support development and validation.

When the first release is ready, configure a PyPI Trusted Publisher for this
repository and a narrowly scoped tagged-release workflow using GitHub OIDC.
That setup, initial release preparation, and publication are separate from the
[GitHub security setup](github.md). A public GitHub repository is not a prerequisite
for uploading distributions to PyPI.

`uv build` creates local wheel and source distributions. `just install-check`
installs those artifacts in disposable environments using uv. These operations
do not register or publish a package.
