# Configuration and file reference

## Configuration

`assignment.json` defines criterion keys, labels, score maxima, weights, assessment scopes, anonymous grader aliases and disagreement thresholds. A criterion defaults to integer scores from zero through `max_score`. An optional `allowed_scores` list defines a different scoring ladder, such as `[0, 1, 2, 2.5, 3]`. The ladder is ascending and includes `max_score`. A weight is a positive relative share.

Each criterion requires a `scope`:

| Value | Meaning |
| --- | --- |
| `submission` | One score, feedback and evidence assessment shared by every member. |
| `individual` | A separate score, feedback and evidence assessment for each member. |
| `unresolved` | The assignment policy has not decided the scope. Configuration can load, but preparation fails until the scope is resolved. |

The weighted percentage for each member is `100 × sum(score / max_score × weight) / sum(weight)`. The tool rounds it once to `rounding_digits` decimal places, with a default of one. The calculation includes every shared criterion and the member's own individual criteria. The raw total sums the same scores.

`policy_confirmed` prevents preparation until setup has been reviewed. `baseline_required` determines whether a nonempty baseline is mandatory. `snapshot_policy` supports explicit submitted commits, a configured cutoff, or branch head. See the [setup guide](../template/SETUP.md) for exact eligibility semantics.

`review_thresholds.criterion_spread` and `review_thresholds.total_spread` are raw-point thresholds for the ledger. `weighted_percentage_points` controls the stage guide's threshold for weighted score impact and defaults to one point. A large spread remains visible after arbitration as context.

## Workspace files

| Path | Owner and purpose |
| --- | --- |
| `assignment.json`, `input/` | TA supplies rubric configuration, brief, policy, roster, diagnostics and pristine baseline before preparation. |
| `manifest.json` | Tools record frozen inputs and either exact Git commits or local content fingerprints. |
| `repositories/` | Tools cache Git repositories during acquisition. |
| `snapshots/ID/` | Tools pin detached Git trees or copies of local directories and ZIP files. |
| `schemas/` | Tools derive evaluation and verdict schemas from the configured rubric. |
| `evaluations/grader_A/ID.json` | Assigned anonymous grader writes shared assessments, individual assessments and an overall remark. |
| `evaluations/arbiter/ID.json` | Arbiter writes shared and individual assessments, an overall remark, evidence and internal rationales. |
| `evidence/ALIAS/ID/` | Each reviewer records commands, environment, observations and supporting logs. |
| `coverage/ALIAS/ID.json` | Grader records for the optional frozen verification checklist. |
| `corrections/ID/001.json` | Accepted corrections link to the previous effective verdict digest and preserve the full replacement. |
| `verification/ID.json` | Sanity reviewer records factual checks and the current proposal digest. |
| `review/ID.json` | Human TA records review notes bound to the current results and sanity review. |
| `report_assets/` | Report CSS and JavaScript. The workspace copies them at initialisation and fingerprints them at preparation. Reports embed their assets. |
| `reports/` | Derived internal HTML, comparison CSV, proposed gradebook and complete review state. |
| `exports/` | Approved gradebook, private roster feedback archives, individual feedback files and receipt; generated only after complete current human approval. |
| `scratch/` | Separate runtime copies owned by each reviewer. |
| `private/run-preferences.json` | Operator settings for models, delegation, tools, machines and report triggers. |
| `revision/` | Parent revision, affected scope and provenance for carried records. |
| `remote_tasks/` | Issued task manifests and returned remote evidence with import receipts. |

## Roster and attribution

The roster columns are `submission_id,student_id,name,github_username,repo_url,branch,commit`, in that order. Each row represents one member. Rows with the same submission ID form one submission and must agree on source fields. Student IDs are unique. GitHub usernames are optional and unique when supplied. Each member has an explicit name and student ID.

Submission and student IDs use letters, digits, underscores or hyphens. GitHub usernames use the account handle without `@`. For a local directory or ZIP file, `repo_url` contains the path and `branch` and `commit` are blank. One snapshot is prepared per submission, regardless of membership count.

A Git submission needs an eligible commit. A local directory or ZIP uses a content fingerprint. The course defines eligibility for local files and how to handle missing work. The assignment policy defines permissible individual evidence.

## Evaluation records

Every candidate evaluation and arbiter verdict contains `submission_id`, `author`, `manifest_digest`, `commit`, `scores`, `individual_scores`, `summary` and `flags`. `summary` is the required overall student-facing remark. It supplements component feedback and must describe the verdict's strengths, gaps and priorities for follow-up under the assignment policy. For groups, it addresses shared work only because every member receives it.

`scores` contains exactly the criteria whose scope is `submission`. `individual_scores` contains exactly the submission's student IDs when individual criteria exist. Each member's object contains exactly the individual criterion keys. When there are no individual criteria, `individual_scores` is `{}`. An all-individual rubric uses `scores: {}`.

Each criterion assessment contains `score`, `feedback` and `evidence`. Arbiter assessments also require an internal `rationale`. Scores follow the configured scoring ladder or integer range. Feedback and the overall remark must be nonempty. Evidence must contain at least one reference. Generated schemas enforce criterion keys and exact member coverage.

`flags` records internal concerns. A missing contribution record is a policy or evidence issue for TA review, not permission to invent an individual score. The [grading manual](../template/GRADING_MANUAL.md) defines evidence handling and feedback instructions.

## Reports and exports

`reports/review.html` uses the Columns design with a fixed viewport and internal scrolling. Select a submission in the queue, a member in the attribution strip and a rubric row to inspect its feedback. Search covers submission IDs, student IDs, student names and optional GitHub usernames. Filters include approval status, missing graders, configured disagreement thresholds and submission type. The differences filter includes disagreements in scores, feedback or evidence, as well as missing evaluations.

Each member's raw total and weighted percentage are shown separately. The rubric supports all configured graders and maximum scores. The feedback inspector preserves candidate evidence and the effective verdict's arbitration rationale. Overall remarks and TA review have separate tabs. TA review includes the pinned source commit, missing records, sanity checks, correction history and current approval state. A large candidate spread remains visible after a human-approved verdict.

The HTML is self-contained and opens offline with JavaScript enabled. No student or grading data is fetched over the network. Reports embed IBM Plex Mono for monospace text and fall back to the system monospace font. Opening, filtering or selecting content cannot approve a submission or export grades; use the CLI for those actions.

`reports/comparison.csv` contains the detailed comparisons. `reports/proposed-grades.csv` and `exports/approved-grades.csv` have one row per student. Approval remains submission-level and covers every member's assessments.

`exports/student-feedback.json` is a private array of member results. Each entry contains `submission_id`, `student_id`, `name`, `github_username`, `members`, `commit`, `scores`, `summary`, `raw_total` and `weighted_percentage`. `members` lists submission identities. `scores` contains the shared assessments and only that member's individual assessments, with `score`, `feedback` and `scope` for each criterion. Internal rationale, evidence logs and candidate grader identities are excluded.

`exports/student-feedback.html` is the private whole-roster archive. `exports/feedback/SUBMISSION_ID/STUDENT_ID.html` is the recipient's standalone feedback file. It includes the shared overall remark, shared component feedback and only that recipient's individual component feedback. Only the recipient's file is intended for sharing. The export command does not send any file.

Generate a fictional ledger with the [demo in the README](../README.md#try-the-demo).

## Commands

Run `python grading.py --help` for the current CLI. `--workspace PATH` is a global option that defaults to the current directory.

- `init DESTINATION` creates a workspace and refuses an existing destination. Run it from the Markwire repository.
- `setup [--review]` opens the setup wizard or reviews a saved draft.
- `import-roster --csv FILE --submission-column HEADER --student-column HEADER` maps CSV headers before the input freeze. The GitHub and commit column flags are optional. `--id-column` is an alias for `--submission-column`.
- `prepare` validates setup, fetches branches, selects source commits, freezes inputs and generates schemas. Completed reruns do not fetch.
- `prompt --role grader|arbiter|sanity` prints the short role prompt. Graders need `--grader grader_A`. `--submission ID` limits the scope to one submission and all its members.
- `template --role grader|arbiter|sanity|correction --submission ID` prints an incomplete JSON skeleton. Every empty required field and null score needs a value before saving. Grader records require `--grader`.
- `validate` checks record shapes, score ranges, identifiers, source references, correction ancestry and unchanged snapshots. It reports completion separately from validity. It does not prove the truth of feedback.
- `status` lists missing grader and coverage records, arbitration, current sanity review and approval by submission.
- `coverage-template --grader ALIAS --submission ID` generates a record for independent checks.
- `report` renders partial or complete results. Malformed records cause errors; missing records are shown as missing. Stale sanity reviews and human approvals cannot make a grade exportable.
- `stage-report` renders a staff guide. `import-decisions` records a response against the current guide.
- `revise` plans or creates a sibling revision. `bundle` issues and imports a detached task. See the [revision and hand-off guide](ongoing-grading.md).
- `approve --submission ID --reviewer NAME --notes TEXT` records a completed human review of every member in the submission. Requires every configured grader, arbitration and current resolved sanity checks.
- `export` requires current approval for the entire roster. `export --amendment` requires approval of the affected submissions in a revision. Both generate a gradebook, student feedback and a receipt.

`--student ID` remains a compatibility alias for `--submission ID`; its value is a submission ID, not a member ID.

## Boundaries

The portable CLI manages grading files and provenance. Model dispatch is manual or uses the [T3 operator workflow](t3-orchestration.md). Reviewers evaluate the factual support for evidence references. Filesystem isolation requires separate workspace views.

Pinned Git commits identify the assessed tree. The course decides whether Git timestamps are acceptable evidence of submission time. Submodules and Git LFS delivery need separate materialisation by the teaching team.

A policy, rubric, roster, baseline or tool change after preparation requires a new workspace revision. Reusing an existing manifest after editing inputs is rejected. Old exports remain historical files; rerun export and inspect its receipt before entering grades. Filenames for corrections sort lexicographically, so use fixed-width numbers.

The roster and scoped-assessment contract applies to newly initialised workspaces. Frozen older workspaces keep their copied tools and original record shape. In-place migration of a frozen workspace is unsupported. Replacing old engine files would invalidate its recorded input/tool fingerprint.

Local anonymous grading uses cooperative separation. For stronger isolation, give graders only their assigned inputs and merge their result directories afterward. Keep the alias-to-model mapping in the operator's private settings.
