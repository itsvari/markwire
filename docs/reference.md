# Configuration and file reference

## Configuration

`assignment.json` defines criterion keys, labels, score maxima, weights, assessment scopes, anonymous grader aliases, and review thresholds. By default, a criterion accepts integer scores from `0` to `max_score`. An optional `allowed_scores` list defines a custom ascending ladder (such as `[0, 1, 2, 2.5, 3]`) that must include `max_score`. Weights are positive relative shares.

Every criterion requires an explicit `scope`:

| Value | Meaning |
| --- | --- |
| `submission` | One shared score, feedback, and evidence assessment applying to every member of the submission. |
| `individual` | A separate score, feedback, and evidence assessment for each named member. |
| `unresolved` | The assignment policy has not yet set the scope. The configuration can load, but `prepare` refuses to run until every scope is resolved. |

Each member's weighted percentage is `100 × sum(score / max_score × weight) / sum(weight)`, rounded once to `rounding_digits` decimal places (default: `1`). The calculation combines every shared criterion with that member's own individual criteria. The raw total sums the same scores.

`policy_confirmed` blocks `prepare` until the teaching team has reviewed setup. `baseline_required` controls whether `input/baseline/` must contain starter files. `snapshot_policy` selects explicit submitted commits, a configured deadline cut-off, or branch head. See the [setup guide](../template/SETUP.md) for full eligibility rules.

`review_thresholds.criterion_spread` and `review_thresholds.total_spread` set raw-point disagreement thresholds for the Columns ledger. `review_thresholds.weighted_percentage_points` sets the stage report threshold for weighted score impact and defaults to `1` percentage point. Large candidate spreads remain visible after arbitration and approval as review context.

## Workspace files

| Path | Owner and purpose |
| --- | --- |
| `assignment.json`, `input/` | TA supplies the assignment configuration, brief, rubric, policy, roster, optional verification checklist, optional diagnostics, and pristine baseline before preparation. |
| `manifest.json` | Tool records frozen input hashes alongside pinned Git commits or local content fingerprints. |
| `repositories/` | Tool caches bare Git repositories during source acquisition. |
| `snapshots/ID/` | Tool pins detached Git trees or copies of local directories and ZIP archives. Never edit these directly. |
| `schemas/` | Tool generates JSON Schemas for grader evaluations and arbiter verdicts from the configured rubric and roster. |
| `evaluations/grader_A/ID.json` | Assigned anonymous grader writes shared assessments, individual assessments, overall remark, and flags. |
| `evaluations/arbiter/ID.json` | Arbiter writes reconciled shared and individual assessments, overall remark, evidence, and internal rationales. |
| `evidence/ALIAS/ID/` | Each role stores commands, environment details, observations, and supporting logs. |
| `coverage/ALIAS/ID.json` | Each grader records independent observations for the optional frozen verification checklist. |
| `corrections/ID/001.json` | Accepted corrections chain from the previous effective verdict digest and store a full replacement verdict. |
| `verification/ID.json` | Independent sanity reviewer records factual checks bound to the current proposal digest. |
| `review/ID.json` | Human TA records approval notes bound to the current evaluation bundle and sanity review. |
| `decisions/` | Tool stores validated decision receipts imported from stage reports. |
| `report_assets/` | CSS, JavaScript, and embedded fonts copied at initialisation and fingerprinted at preparation. |
| `reports/` | Generated internal Columns ledger (`review.html`), stage reports (`STAGE.html` / `STAGE.json`), comparison CSV, proposed gradebook, and review state JSON. |
| `exports/` | Approved gradebook, private roster feedback archive, standalone per-student feedback files, and export receipt; written only after complete current human approval. |
| `scratch/` | Separate runtime copies owned by each reviewer for building and running student code. |
| `private/run-preferences.json` | Operator-only settings for models, delegation, tool permissions, remote machines, and stage report triggers. |
| `revision/` | Parent revision link, reassessment scope, and provenance for carried assessments. |
| `remote_tasks/` | Issued remote task manifests and imported evidence with verification receipts. |

## Roster and attribution

`input/roster.csv` uses the exact column headers `submission_id,student_id,name,github_username,repo_url,branch,commit`. Each row represents one student. Rows sharing a `submission_id` form one group submission and must match on `repo_url`, `branch`, and `commit`. Student IDs must be unique. GitHub usernames are optional, omit `@`, and must be unique when provided. Every member requires a non-empty name and student ID.

Submission and student IDs accept ASCII letters, digits, underscores, and hyphens. For a local directory or ZIP archive, put the path in `repo_url` and leave both `branch` and `commit` blank. Markwire prepares one snapshot per submission regardless of group size.

Git submissions require an eligible commit; local directories and ZIP archives use a deterministic content fingerprint. Course policy determines eligibility for local archives, handling of missing submissions, and permissible evidence for individual criteria.

## Evaluation records

Every grader evaluation and arbiter verdict contains `submission_id`, `author`, `manifest_digest`, `commit`, `scores`, `individual_scores`, `summary`, and `flags`.

`summary` is the required student-facing overall remark. It complements component feedback by stating key strengths, consequential gaps, and follow-up priorities permitted by the assignment policy. For group submissions, keep `summary` strictly about shared work because every member receives it.

`scores` must contain exactly the criteria with scope `submission`. When individual criteria exist, `individual_scores` must contain an entry for every member's `student_id`, each containing exactly the `individual` criterion keys. When no individual criteria exist, `individual_scores` is `{}`. An all-individual rubric uses `"scores": {}`.

Each criterion assessment contains `score`, `feedback`, and `evidence`; arbiter assessments also require an internal `rationale`. Scores must match the configured integer range or `allowed_scores` ladder. Feedback, rationales, and overall remarks must be non-empty text, and `evidence` must list at least one reference. Generated schemas in `schemas/` enforce criterion keys and exact member coverage.

`flags` records internal concerns for arbitration or TA review. Missing contribution records are a policy or evidence issue for the TA, never licence to invent an individual mark. See the [grading manual](../template/GRADING_MANUAL.md) for evidence and feedback rules.

## Reports and exports

`reports/review.html` uses the Columns layout with a fixed viewport and independent scrollable panels. Choose a submission from the queue, focus on all members or one student in the attribution strip, and select any rubric row to inspect candidate feedback, evidence, and arbitration rationale. Search matches submission IDs, student IDs, student names, and optional GitHub handles. Filters cover approval state, missing graders, configured disagreement thresholds, and solo or group submissions. **Differences only** shows rows with differing scores, feedback, or evidence, as well as missing candidate evaluations.

Raw totals and weighted percentages are shown separately for each member. Separate tabs display **Overall remarks** and **TA review** (including linked stage reports, pinned source metadata, candidate coverage, sanity review checks, correction history, and approval state). Large candidate spreads stay visible even after human approval.

Stage reports (`reports/STAGE.html`, paired with machine-readable `reports/STAGE.json`) follow the same offline architecture. Each stage report groups findings across topics and provides a single **Decision hand-off** packet covering every open finding with options. **Defer** counts as a completed choice where offered, custom choices require written instructions, and copying or downloading JSON stays disabled until every open finding with options is answered. Importing the JSON via `import-decisions` rejects partial or stale packets and saves a receipt under `decisions/`.

All HTML reports are self-contained and open offline with JavaScript enabled; no student or grading data leaves the browser. Reports embed IBM Plex Mono for monospace text and fall back to the system monospace font. Opening, filtering, or copying from a report never approves grades or triggers an export—use the CLI for those actions.

`reports/comparison.csv` provides per-criterion comparisons across graders. `reports/proposed-grades.csv` and `exports/approved-grades.csv` contain one row per student. Human approval is recorded at submission level and covers every member's assessments.

`exports/student-feedback.json` is a private array of member records containing `submission_id`, `student_id`, `name`, `github_username`, `members`, `commit`, `scores`, `summary`, `raw_total`, and `weighted_percentage`. Each member's `scores` contains shared assessments plus only that member's individual assessments (`score`, `feedback`, and `scope`), excluding internal rationales, evidence logs, and candidate grader aliases.

`exports/student-feedback.html` is a private whole-roster archive. `exports/feedback/SUBMISSION_ID/STUDENT_ID.html` is each recipient's standalone feedback page containing the shared overall remark, shared component feedback, and only that recipient's individual feedback. Distribute only the recipient's standalone file; `export` writes local files and never transmits them.

Generate a fictional example with the [demo in the README](../README.md#try-the-demo).

## Commands

Run `python grading.py --help` to inspect the full CLI. `--workspace PATH` is a global option defaulting to the current working directory.

- `init DESTINATION` creates a self-contained assignment workspace and refuses an existing path. Run from the Markwire checkout.
- `setup [--review]` launches the interactive setup wizard or validates and previews a saved draft.
- `import-roster --csv FILE --submission-column HEADER --student-column HEADER` normalises a roster CSV before preparation. `--github-column`, `--branch-column`, and `--commit-column` are optional; `--id-column` is an alias for `--submission-column`.
- `prepare` validates setup, fetches Git branches or stages local archives, pins snapshots, freezes inputs, and generates JSON Schemas. Completed re-runs verify existing snapshots without re-fetching.
- `prompt --role grader|arbiter|sanity` prints the role prompt. Graders require `--grader ALIAS`. `--submission ID` narrows the prompt to one submission and all its members.
- `template --role grader|arbiter|sanity|correction --submission ID` prints a JSON skeleton with empty required fields and `null` scores to complete before saving. Graders require `--grader ALIAS`.
- `validate` verifies record structure, score scales, identifiers, source references, correction chains, and unmodified snapshots. It reports readiness and approval separately from structural validity, and does not verify factual truth.
- `status` prints per-submission JSON showing missing graders, missing verification coverage, arbitration, current sanity review, readiness, and approval.
- `coverage-template --grader ALIAS --submission ID` generates or refreshes an independent verification coverage record from `input/verification.json`.
- `report` renders the Columns ledger (`reports/review.html`), comparison CSV, proposed gradebook, and `reports/state.json` from partial or complete validated records. Malformed records fail; missing records stay explicitly missing.
- `stage-report --stage STAGE` generates an offline staff stage report (`reports/STAGE.html` and `reports/STAGE.json`), or prints draft findings with `--draft` for optional `--curation FILE` synthesis.
- `import-decisions FILE --reviewer NAME` validates and records a complete stage-report decision packet under `decisions/`, rejecting partial, malformed, or stale packets.
- `revise plan|create` plans or creates a targeted sibling revision workspace. `bundle pack|open|finish|import` packages and imports detached remote checks. See the [revision, remote-check, and decision guide](ongoing-grading.md).
- `approve --submission ID --reviewer NAME --notes TEXT` records human TA review for a submission and all its members. Requires every configured grader, required coverage, arbitration, and a current resolved sanity review.
- `export` exports the approved gradebook, feedback archives, standalone student files, and SHA-256 receipt once every submission has current approval. `export --amendment` exports only the affected submissions in a revision workspace.

`--student ID` remains a compatibility alias for `--submission ID`; its value is always a submission ID, not a member ID.

## Boundaries

The portable CLI manages grading records and provenance; model dispatch is either manual or orchestrated through the optional [T3 orchestration runbook](t3-orchestration.md). Reviewers remain responsible for checking whether cited evidence factually supports claims. Filesystem isolation requires separate workspace copies.

Pinned Git commits identify the assessed tree. The course decides whether Git commit timestamps are acceptable proof of submission time. Submodules and Git LFS objects require explicit materialisation by the teaching team before preparation.

Changing the policy, rubric, roster, baseline, or tool files after preparation requires a new sibling revision workspace; editing frozen inputs in place invalidates the manifest. Previous exports remain historical artefacts—re-run `export` after changes and check `receipt.json` before entering marks. Correction filenames sort lexicographically, so use fixed-width zero-padded numbers (`001.json`, `002.json`).

The roster and scoped-assessment contract applies to newly initialised workspaces. Older frozen workspaces retain their copied tools and original record shape; in-place migration is unsupported because replacing engine files invalidates the recorded input fingerprint.

Local anonymous grading relies on cooperative separation. For stronger isolation, give each grader a separate workspace copy containing only its inputs and merge result directories afterwards. Keep the alias-to-model mapping strictly inside `private/run-preferences.json`.
