# Notebook inspection schema

`notebooks inspect` and `cli.main` expose a supported read-only inventory of
nbformat 4 files. Internal loaders are not supported Python interfaces. See the
[README](../README.md#notebooks) for consumer commands and source-preview controls.

Inspection uses only standard-library parsing and the base package. It never
loads optional Jupyter dependencies, normalizes source files, generates IDs,
converts notebook versions, executes cells, or prints stored outputs and metadata.
Source arrays are joined in memory only to count lines and prepare previews.

Input must be UTF-8 JSON with an object root, integer `nbformat = 4`, and a `cells`
array. Unreadable files, other major versions, duplicate JSON keys, non-finite
numbers (including exponent overflow), unpaired Unicode surrogates, ambiguous path selections, and invalid
JSON fail with status 1 and stderr diagnostics. Every input is read before stdout
is emitted; a failed selection produces no partial JSON report. Paths must be
distinct existing `.ipynb` files and resolve against the consumer root.

Inspection tolerates missing or older minor versions, missing/invalid/duplicate
cell IDs, non-object cells, malformed source fields, and missing metadata or code
fields. It reports these as repair problems and returns zero when the inventory
is complete. This is a focused structural inventory, not exhaustive nbformat
schema validation: output payloads, attachments, metadata contents, and unknown
fields are not validated. A problem-free inventory is not proof that strict
`check`, `lint`, `clear`, `advise`, or `execute` will admit the file.

## JSON version 1

The top-level object contains integer `schema: 1` and `notebooks`, an array in
selection order. Future compatible additions may add fields; consumers should
ignore unknown fields and reject unknown schema versions. Human-readable text and
problem message wording are not machine interfaces.

Each notebook contains:

| Field | Type and meaning |
| --- | --- |
| `path` | String; absolute resolved platform-native path |
| `nbformat` | Integer `4` |
| `nbformat_minor` | Existing integer, or `null` when missing or not an integer |
| `problems` | Array of human-readable notebook-level repair messages |
| `cells` | Array containing one entry for every original cell position, including malformed entries |

Each cell contains:

| Field | Type and meaning |
| --- | --- |
| `number` | Integer; one-based position among all original cells |
| `cell_type` | Existing string, or `null` when missing or not a string |
| `id` | Existing string exactly as stored, or `null` when missing or not a string; never a generated ID |
| `id_status` | `missing` if absent; `invalid` if not 1–64 ASCII letters/digits/underscores/hyphens; `duplicate` if a valid ID repeats an earlier cell; otherwise `existing` |
| `source_lines` | Integer from joined source's `splitlines()` (`0` for empty source), or `null` if source is malformed |
| `output_count` | Array length for a code cell with array outputs; otherwise `null`; contents are never included |
| `execution_count` | Existing nonnegative integer for code cells; otherwise `null`; missing/invalid code counts also receive a problem message |
| `problems` | Array of human-readable repair messages |
| `preview` | Up to 80 characters of whitespace-collapsed source, or `null` for malformed source; key omitted entirely with `--no-preview` |

The first occurrence of a duplicated valid ID retains `existing` status; later
occurrences are `duplicate`. A non-object cell has `missing` identity plus a
cell-structure problem. Inspection never guesses an ID or cell type. JSON retains
existing string IDs even when invalid; compact text truncates their display to
64 characters. A generated-looking ID is still an existing ID; optional review
advice handles that separate policy concern.

Previews expose source text. `--no-preview` removes all source text from the
inventory, while filenames, string cell IDs, cell types, counts, and repair
messages remain visible. Arbitrary invalid field objects and stored-output or
metadata values are never serialized into diagnostics.
