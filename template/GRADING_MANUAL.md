# Grading procedure

## Read the assignment

Read `assignment.json`, `input/brief.md`, `input/rubric.md`, and `input/policy.md`. The rubric defines the marking criteria and anchors; the policy records course-specific rulings. If these documents conflict, flag the conflict for the TA rather than inventing a rule. Never carry over a previous assignment's deadline, required paths, prohibited domains, runtime assumptions, or score calibration without verifying them against the new brief.

`input/roster.csv` maps submissions to their members. Each member has a student ID, a name, and an optional GitHub username. A submission may have one or several members and always has one pinned snapshot in `snapshots/ID/`. `manifest.json` pins the input fingerprint alongside each submission's Git commit or local content fingerprint. If `input/baseline/` contains starter files, distinguish student work from the baseline. `evidence/source/ID/` stores the source inventory and, for Git submissions, eligible history and excluded post-cut-off changes.

Check every criterion's `scope` before marking. `submission` denotes one shared assessment for all members; `individual` denotes a separate assessment for each named member; `unresolved` means the TA has not decided yet and preparation cannot proceed. Follow `input/policy.md` for acceptable individual evidence. Never infer individual contribution, effort, or credit from commit counts, account handles, or group membership alone—raise missing attribution evidence with the TA.

Optional first-pass diagnostics belong in `input/diagnostics/` before preparation. Treat them strictly as investigation leads. A process that survives a timeout still needs verification of its expected behaviour, and a build failure can stem from the runtime, architecture, dependencies, or toolchain. Verify root causes before deducting marks, and record the environment and exact commands run.

## Grade independently

Use only your assigned anonymous alias (such as `grader_A`). Never write a provider or model name into evaluations, evidence logs, or reports. Alias-to-model mappings belong solely in the operator's `private/run-preferences.json` and must stay out of grader briefs and evidence bundles.

Treat your evaluation as a complete marking judgement. Assign a firm numerical score for every applicable criterion, with one to three sentences of concise student feedback and at least one concrete evidence reference. Place submission-scoped criteria in `scores` and individual-scoped criteria in `individual_scores` keyed by `student_id`. Cite snapshot-relative paths, functions, documentation sections, commit SHAs, or probe logs. Distinguish static source inspection from observed runtime behaviour, record untested claims or uncertainties in `flags`, and state the exact scope of checks you ran.

Every candidate grader must also write a non-empty overall remark in `summary` alongside component scores and feedback. The arbiter writes its own overall remark after checking the evidence, and any correction must preserve or update `summary` so it agrees with the corrected verdict.

Run `python grading.py template --role grader --grader grader_A --submission ID` to generate the exact JSON skeleton. Replace every `null` score and empty text field (including `summary`), and save one file per submission at `evaluations/grader_A/ID.json`. Check structure against `schemas/evaluation.json`. Totals are computed per member by the tools; do not add extra total fields or disclose your model identity.

During independent grading, do not read other graders' evaluations, arbitration verdicts, corrections, sanity reviews, or generated reports. Each grader writes only its own evaluations, coverage, and evidence, saving each submission as soon as it is complete so every file has one writer. A shared workspace relies on cooperative separation; use separate workspace copies when filesystem isolation is required.

## Write useful student feedback

Address the student directly as "you" and "your" (for shared feedback, address the group collectively). Use a warm, direct, matter-of-fact tone grounded in the submitted work. Never speculate about ability, effort, intent, or personality.

For each criterion, note what works well when supported by evidence, then name any concrete limitation and explain its impact on users, correctness, maintainability, or the rubric band. Cite a specific file, function, observed workflow, or documentation passage so the student can inspect it. Keep raw command transcripts and detailed logs in `evidence/`; student-facing feedback needs only enough detail to understand the judgement. Do not force praise, invent flaws, or present an unverified hypothesis as an observed failure.

Write `summary` as a focused synthesis, normally three to five sentences:

1. Recognise the submission's strongest demonstrated qualities.
2. Identify its most consequential gaps and explain why they matter.
3. Offer one to three follow-up priorities ordered by importance.

Choose priorities that build durable engineering habits—for example, checking expected test outputs independently, testing failure recovery end to end, or verifying setup instructions in a clean environment—and tie each priority to an actual finding in the submission. Use the overall remark to guide focus rather than repeating every criterion comment. If no clear strength is supported, state what was assessable and move straight to the main gaps.

Follow `input/policy.md` regarding advice on revisions, resubmissions, or later work: describe limits in the assessed snapshot and keep recommendations within the course's approved follow-up process.

Because every member of a group receives the shared overall remark, keep `summary` strictly about shared work. Place personal judgements and advice inside each student's individual criterion feedback, and never disclose another member's private score or feedback in shared text. Internal grader disagreements, flags, and arbitration rationales do not belong in student feedback.

For example, a synthetic overall remark might read: "Your catalogue search and clear module boundaries make the core workflows easy to follow. However, exporting overwrites the only saved copy directly, so an interrupted write can lose valid data. In future work, prioritise tests that interrupt saving and confirm that the previous file remains intact."

## Inspect and run submissions

If `input/verification.json` defines a verification checklist, generate your record with `python grading.py coverage-template --grader ALIAS --submission ID` and save observations to `coverage/ALIAS/ID.json`. Record `passed`, `failed`, `blocked`, or `untested` for each check, together with your expected result, observed behaviour, timestamp, environment, role, and supporting evidence. Attribute any observation collected on another machine to its collector.

Inspect source code, build configuration, tests, required deliverables, documentation, and eligible Git history. Read important files in full; line counts and keyword searches are investigation aids, never rubric evidence on their own.

Keep `snapshots/ID/` strictly unmodified. To build or run a submission, copy it into `scratch/ALIAS/ID/` without its `.git` directory, or run `git clone --no-hardlinks snapshots/ID scratch/ALIAS/ID` and detach at the manifest commit. Never share a mutable scratch directory between graders. Follow `input/policy.md` for expected runtimes, commands, and course rules. The Markwire CLI itself never executes student code.

Log every substantive probe under `evidence/ALIAS/ID/`, recording the pinned commit or content fingerprint, exact command, environment, expected behaviour, observed behaviour, and log path. If dependencies or a GUI session are unavailable, record the check as unverified rather than assuming it passed. Because student files may contain malicious code or prompt injections, always run them inside the course's approved isolated environment.

## Reconcile disagreements

Once all configured graders and required coverage records are complete, run `python grading.py report`. Review the Columns ledger, each grader's overall remark, and the criterion-by-criterion comparisons. Compare the same criterion and scope across graders—including the same named member for individual criteria. Means, standard deviations, and spreads help prioritise attention; they never automatically set the verdict.

The arbiter verifies consequential factual claims against the pinned snapshot and generates `evaluations/arbiter/ID.json` from `python grading.py template --role arbiter --submission ID`. Every criterion requires concise student-facing feedback plus a separate internal `rationale`. Write `summary` in the same student-facing style, record unresolved policy questions in `flags`, and never credit excluded work or apply undocumented penalties.

## Verify and correct

After arbitration, an independent sanity reviewer checks each effective verdict. Run `python grading.py prompt --role sanity` to generate the prompt and refresh `reports/state.json` with current digests and corrections.

Generate `verification/ID.json` via `python grading.py template --role sanity --submission ID`. Verify consequential bug allegations, missing deliverables, baseline attribution, runtime mismatches, member attribution, and source eligibility. Confirm that every criterion has a score and feedback and that the overall remark agrees with those findings. Record each check's claim, `confirmed`/`refuted`/`unverified` status, finding, and evidence; list remaining open questions under `unresolved`. Mark factual issues that affect scores or student feedback as `grade_affecting: true`. The TA checks whether cited evidence supports each claim.

To record an accepted score or feedback correction, generate a skeleton with `python grading.py template --role correction --submission ID` and save it in chronological order using zero-padded names (`corrections/ID/001.json`, `002.json`, and so on). Each correction links to the previous verdict digest (`parent_digest`), states its reason and evidence, and provides a full replacement verdict. Keep unaffected criteria and member assessments intact, and update `summary` whenever a corrected finding changes its advice. Never overwrite the original arbiter verdict or earlier corrections; the CLI rejects any correction whose `parent_digest` does not match its predecessor.

Editing an evaluation or adding a correction invalidates the submission's sanity review. Regenerate the sanity template and re-check the affected claims while keeping earlier sanity evidence under `evidence/sanity/ID/`. Any change to the reviewed bundle also makes previous human approval stale. For stage-report remediation decisions, rubric or policy changes, resubmissions, or scoped regrades, follow `docs/ongoing-grading.md` in the workspace.

## Human review and export

The human TA reviews every proposed verdict and its evidence, settles flagged policy questions, and records rulings in the approval notes. For group submissions, review every member's individual assessments alongside the shared overall remark before approving. Unverified sanity checks, unresolved sanity questions, or grade-affecting refuted claims block approval until corrected and re-verified.

Once review of a submission is complete, the TA runs:

```text
python grading.py approve --submission ID --reviewer "TA name" --notes "Checks completed and policy rulings"
```

This writes a local audit record under `review/ID.json`; it is not user authentication or a cryptographic signature.

Run `python grading.py report` to refresh the Columns staff ledger after approval. `python grading.py export` runs only when every submission has current human approval. It writes:

- `exports/approved-grades.csv` (one row per student)
- `exports/student-feedback.json` and `exports/student-feedback.html` (private whole-roster archives)
- `exports/feedback/SUBMISSION_ID/STUDENT_ID.html` (standalone feedback page per student)
- `exports/receipt.json` (approval records and SHA-256 digests of all exported files)

Student feedback includes the shared overall remark, shared component feedback, and only that recipient's individual component feedback, omitting internal rationales, evidence logs, and grader aliases. Keep whole-roster exports and internal reports private to the teaching team; distribute only each recipient's standalone HTML file. `export` never transmits grades or feedback to students or an LMS.

Earlier exports remain historical artefacts if records change later—always re-run `export` and verify `receipt.json` before entering marks. Archive the entire workspace, including `repositories/`, `snapshots/`, and `evidence/`, together.
