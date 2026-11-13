# Review API and consent

`research_repo_tools.review` owns CodeRabbit invocation and review identity.
`research_repo_tools.review_protocol` owns immutable result and approval records.
Consumer usage is in the [README](../README.md#coderabbit-reviews); routine
commands remain opt-in and outside checks. See [release availability](../README.md#release-availability)
before adopting these interfaces.

## Calls and results

`start(root: Path, *, base: str | None, pending_path: Path | None = None,
output: Callable[[bytes], None] = ..., timeout: float | None = None) -> ReviewResult`
starts one invocation without `--use-credits`. `base=None` selects uncommitted
changes; otherwise it selects branch and local changes against that base.
Both include non-ignored untracked paths and pass `AGENTS.md` plus exactly one
regular `.coderabbit.yaml` or `.coderabbit.yml` as additional instructions.
Instruction symlinks are rejected. `origin/main` must match the live origin
main ref; an explicit other base is resolved locally. RRT's Git observations are
read-only and never fetch. CodeRabbit can persist its own base preference in Git
configuration and maintain its review cache.

`output` receives NDJSON bytes as available; a missing final LF is added between
invocations and before the wrapper result. Existing bytes are preserved, and
stderr remains inherited. The callback must return promptly and treat findings,
paths and command strings as untrusted data. A positive finite timeout limits each direct child invocation;
`None` waits indefinitely. Cancellation terminates that child. Descendants are
not a managed process group. Events are bounded to 1 MiB per line and 64 MiB
total. Native byte transport preserves CRLF and split UTF-8 sequences.

| Result | Meaning |
| --- | --- |
| `CompletedReview(findings, reviewed_files, reused, skipped)` | Successful completion or no-change skip; findings can be nonzero. A reused result is not evidence of a new review. |
| `ConsentRequired(quote, pending_path, pending_identity, repository, base, head, base_commit, content_identity, selected_paths, branch)` | Stopped for consent, with a new saved selection. `selected_paths` counts the conservative local inventory, **not** billable files or full PR changes. |
| `FailedReview(exit_code, reason)` | Provider failure, incomplete output, interruption or invalidated review. |
| `ReviewDenied(pending_identity)` | Explicit refusal consumed the selection without a provider call. |
| `UncertainReview(reason)` | A paid invocation was attempted; completion, validity or billing needs external reconciliation. Never retry it automatically. |

Local precondition, filesystem and Git errors raise `ValueError`, `OSError` or
`subprocess.SubprocessError`. Provider transport/protocol failures return
`FailedReview`. After paid dispatch, failures become `UncertainReview`, including
output callback errors. Results do not claim that findings have been fixed.

The CLI forwards provider NDJSON, then emits a terminal JSON line with
`type="rrt_review_result"` and `schema="research-repo-tools/review-result/v1"`.
Exit codes are 0 for completion, 3 for consent required, 4 for denial and 5 for
uncertain paid work. Provider failures preserve their nonzero status; signals map
to 128+signal, interruption to 130, timeout to 124, and local errors to 1. Thus an
exit code alone does not distinguish every state; inspect the terminal record.
Local precondition errors have stderr diagnostics and no terminal JSON guarantee.

## Provider evidence and compatibility

CodeRabbit 0.7.6's installed consent emitter provides these fields:

| Provider field | Interpretation |
| --- | --- |
| `type`, `status`, `action` | `action_required`, `awaiting_confirmation`, `rerun_with_use_credits` |
| `billableFilesCount` | Optional nonnegative integer; missing is unknown |
| `maximumCostCents` | Optional nonnegative integer in the provider's hundredths-of-currency representation |
| `confirmationHeadCommitId` | Optional provider content identifier; not assumed to be Git HEAD or a reusable authorization token |
| `promotionTerms` | Optional terms retained in quote identity |
| `command` | Untrusted explanatory text; never executed |

That emitter omits ISO currency and initial/incremental review kind. These remain
explicitly unknown in wrapper output. Never infer currency from `$`, review kind
from file count, or billable count from the full PR. `ReviewQuote(payload: bytes)`
retains a validated consent event, including unknown fields; `identity` hashes
those bytes. `terms()` requires the supported action, count, price and full
provider identifier before resume, and raises on absent or ambiguous terms.

The [official agent contract](https://docs.coderabbit.ai/cli/agent-mode) establishes
reliable failure exits starting at 0.7.7. RRT permits 0.7.6 quote-and-stop but
rejects its completion as unproven and disallows paid resume. Use 0.7.7 or newer
for complete workflows. The [current command reference](https://docs.coderabbit.ai/cli/reference)
documents 0.9.5. Additive events/fields are tolerated; malformed terminal states,
failed outcomes, unreviewed files, missing completion, and nonzero exits are not
clean reviews. `completed_with_warnings` with no missing files can succeed.
Consent events permit provider exit 0 or 1; other exits and signals invalidate
the quote instead of creating resumable state.
Failed local result persistence alone does not imply server review failure.
Version changes invalidate saved content approval; RRT does not install or update
the provider. New incompatible protocols require an adapter update.

## Approval and one-use resume

`resume(pending_path: Path, approval: ReviewApproval, *, output=..., timeout=None)`
requires a separate `ReviewApproval(pending_identity, currency, review_kind,
terms_source, accept_unenforced_quote)`:

- `pending_identity` is the exact SHA-256 shown with the saved quote. It binds
  location, nonce, repository, base, observed content, provider and all quote fields.
- `currency` is the independently confirmed three-letter ISO currency and
  `review_kind` is independently confirmed `initial` or `incremental`.
- `terms_source` records where the caller established those missing facts. An
  arbitrary label does not establish them; remain stopped if they are unknown.
- `accept_unenforced_quote=True` explicitly accepts a new paid invocation whose
  maximum charge and snapshot CodeRabbit does **not** enforce. If approval
  requires a hard cap, this API rejects it and no paid dispatch is available.

The trusted caller obtains the human's consent and enforces consumer restrictions,
including any ban on large initial paid reviews. RRT does not authenticate a
human, establish the truth of external assertions, or impose a consumer file-count
limit. Agent instructions and this wrapper cannot prevent raw CLI bypasses.

Resume checks current inputs, exclusively creates a sibling `.used` receipt,
then refreshes the review **without credits**. Included capacity may now complete
it. A different quote returns a new pending record requiring new consent. Otherwise,
base/content/provider/configuration are checked again immediately before one
argument-array invocation with `--use-credits`. No service command string is used;
no fresh/gate option is added. Failures or crashes leave the receipt consumed.
`deny(pending_path)` creates the same exclusive receipt without invoking CodeRabbit.
Repeated or concurrent use is rejected. Do not delete receipts to retry uncertain
work; reconcile usage externally, then explicitly obtain a new quote and approval.

## Identity and local-state boundary

Review identity uses [native path observations](path-identity-api.md) over all
tracked HEAD/index paths and non-ignored untracked files, plus instruction files.
HEAD-only names are bound as `MissingPath` staged deletions unless they alias a
current observation under Windows path normalization. A case-only rename retains
both HEAD/index spellings in Git metadata without a duplicate native observation.
Replacement files and directory contents retain their own current observations. This supports
staged file/directory replacements without treating directories as file leaves.
It binds staged object IDs/modes, HEAD, branch, base/merge-base commits, Git directory and
effective configuration, provider version/executable bytes, options and inherited
environment (except shell bookkeeping `_`/`SHLVL`). Configuration and environment
values are hashed, not persisted. With an explicit base, the redundant
`coderabbit.basebranch` preference is excluded because CodeRabbit saves it during
review; all other configuration remains bound. Native path bytes are preserved
in identity, including POSIX names that cannot be decoded as UTF-8. Observer and
provider both receive
`GIT_OPTIONAL_LOCKS=0` and `GIT_NO_REPLACE_OBJECTS=1`; replacement objects do not
override approved commit contents. The conservative inventory can invalidate
approval after unrelated local changes. Stop writers while reviewing.

Submodules, unmerged/sparse-directory indexes, nonregular instruction files and
unsupported native entries stop before invocation. Default observation bounds are
64 MiB per selected file, 256 MiB total and 100,000 paths. The inventory does not
freeze Git, filesystem contents or provider uploads; local rechecks cannot close
the race between observation, upload, server selection and billing. Account and
server settings remain external; keep continuation **On demand**. RRT neither
inspects nor changes billing settings and cannot prevent charges under other modes.

Pending JSON (`research-repo-tools/pending-review/v1`) and the `.used` receipt
(`research-repo-tools/review-use/v1`) live outside the Git worktree. Creation is
exclusive, receipt contents are flushed before an attempt, and pending JSON is
limited to 2 MiB on both write and read, including JSON escaping. Moving or
editing a pending file invalidates its identity. Parent directories
and local storage are trusted. A caller who can alter state can bypass this
cooperative control; it is not a tamper-resistant ledger. Default pending files
use an OS temporary directory outside the checkout. The stdout spool uses the
same temporary root, so an in-checkout temporary root is rejected before invoking
the provider, even with an explicit pending path. Native path strings use JSON escapes
so POSIX surrogateescape names round-trip. Retain state until denial or
reconciliation. Supply
an explicit new `--pending` path through the CLI when durable retention is needed.
