# Building reproducible papers

Set up the consumer environment and its native TeX build before using these
checks. The consumer owns compilation, scientific content and reference-artifact
refresh decisions; this guide covers dates, PDF validation and native discovery.

- [Reproducible papers and native dependency discovery](#reproducible-papers-and-native-dependency-discovery)

## Reproducible papers and native dependency discovery

PDF operations are opt-in. Add `research-repo-tools[papers]==X.Y.Z` to the
consumer's tooling group, using an exact published version containing the
interfaces, refresh its lock and run setup. Source-date and dependency discovery
work with the base package. Keep content, figures, bibliography, source/output
locations, fixed dates and refresh decisions in the consumer.

```toml
[tool.research-repo-tools.papers.documents.example]
tex = "papers/example.tex"
pdf = "target/papers/example.pdf"
identity = "papers/example.tex"
min-pages = 1
require-text = ["Example title", "REFERENCES"]
forbid-text = ['\today']
# reference = "papers/example.pdf" # Enable retained-artifact equivalence.
```

The source must contain exactly one uncommented explicit `\date{July 6, 2026}`
in the canonical English form. Parsing uses a fixed month map and UTC midnight,
with no wall clock or locale dependency. A consumer build recipe can use:

```sh
export SOURCE_DATE_EPOCH="$(just papers source-date --paper example)"
# Consumer recipe compiles into its declared build directory using that environment.
just papers normalize --paper example
just papers check --paper example
just papers check --paper example --reference papers/example.pdf
# Explicit refresh intent, after consumer scientific and build checks:
just papers normalize --paper example --output papers/example.pdf
```

Literal-path commands are also supported: `papers source-date SOURCE.tex`,
`papers check BUILT.pdf --min-pages 1 --require-text TITLE --reference RETAINED.pdf`,
and `papers normalize BUILT.pdf --tex SOURCE.tex --identity STABLE_NAME`.
CLI options override named policy. Normalization defaults to the input PDF;
`--output` explicitly selects a separate destination. Every input and candidate
is validated before replacement, and caught publication failures preserve prior
artifacts using the shared transaction contract. Exclude concurrent writers.

The supported normalization profile is Tectonic's uncompressed XMP timestamps,
document/instance UUIDs and 16-byte trailer IDs. Same-width edits preserve xref
offsets, and the complete candidate must retain page text and geometry. Any
Info creation/modification dates must already match the explicit UTC epoch,
including when stored in compressed objects. Missing fields, unsupported profiles,
encrypted or malformed PDFs fail without replacing output. Structural equivalence
compares per-page text, media/crop boxes, rotation and units; it allows different
native PDF bytes and does not certify rendering or scientific meaning.

Native discovery never installs host packages or modifies process environment:

```sh
just tectonic discover
just tectonic discover --format shell
just tectonic export --file target/tectonic.env
```

Linux/macOS resolve `freetype2`, `graphite2`, `icu-uc`, `libpng` and `zlib` with
pkg-config; Linux also requires `fontconfig` and `openssl`. Existing
`PKG_CONFIG_PATH`, `PKG_CONFIG_LIBDIR` and `PKG_CONFIG_SYSROOT_DIR` are retained;
repeat `--prefix PATH` to discover existing `lib/pkgconfig` and `share/pkgconfig`.
macOS also discovers existing Homebrew metadata and SDK shims. Windows requires
`TECTONIC_DEP_BACKEND=vcpkg`, `VCPKG_ROOT` and an existing
`installed/VCPKGRS_TRIPLET` directory (default `x64-windows-static-md`). Discovery
checks locations/resolution; consumers still verify library versions and ABI.
JSON is the default output on every platform; shell output is POSIX syntax.
Windows callers can apply JSON assignments with PowerShell:

```powershell
$native = just tectonic discover | ConvertFrom-Json
$native.PSObject.Properties | ForEach-Object { Set-Item "Env:$($_.Name)" $_.Value }
```

`tectonic export` appends validated UTF-8 LF assignments to the explicit file or
`GITHUB_ENV`. Missing prerequisites and probe errors return nonzero and publish
no assignments. Provision hosts explicitly in consumer setup/CI before discovery.
See the [API contracts](papers-api.md#paper-date-pdf-and-native-discovery-api) and
[consumer deletion maps](shared-capability-migration.md#paper-and-raw-line-adoption).
