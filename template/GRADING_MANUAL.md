# Grading procedure

## Read the assignment

Read `assignment.json`, `input/brief.md`, `input/rubric.md` and `input/policy.md`. The rubric defines scores and the policy records course-specific rulings. If these sources conflict, record the conflict for the TA rather than inventing a rule. Do not reuse a previous assignment's deadline, required paths, prohibited domains, runtime or score calibration without checking the new brief.

`input/roster.csv` identifies submissions and their members. Each member has a student ID, a name, and an optional GitHub username. A submission can have one or several members and has one pinned source snapshot. `manifest.json` pins the input fingerprint and the submitted commit or local content fingerprint. Inspect `snapshots/ID/`. If `input/baseline/` contains starter material, distinguish student work from the baseline. `evidence/source/ID/` contains the source inventory and, for Git submissions, the history and excluded changes.

Check each criterion's `scope` before grading. `submission` means one shared assessment for every member. `individual` means a separate assessment for each named member. `unresolved` means the TA has not decided, so preparation cannot proceed. Follow the assignment policy for individual evidence. Do not infer individual contribution, effort or credit from commit counts, account names or group membership alone. Raise missing attribution evidence with the TA.

Optional first-pass diagnostics belong in `input/diagnostics/` before preparation. Treat them as investigation leads. A launch that survives a timeout still needs a check of the expected behaviour. A build failure can reflect the runtime, architecture, dependencies or toolchain. Verify the cause before deducting. Record the environment and the exact commands actually run.

## Grade independently

Use only your assigned anonymous alias, such as `grader_A`. Never put a provider or model name in evaluations, evidence summaries or reports. Keep the alias mapping in the operator's private run preferences. Leave it out of grader briefs and evidence bundles.

Treat your evaluation as a complete grading decision. Make a firm numerical score for every applicable criterion, with one to three sentences of concise feedback and at least one concrete evidence reference. Assess submission criteria once in `scores`. Assess individual criteria for every member in `individual_scores`, keyed by student ID. Cite snapshot-relative paths, methods, documentation sections, commit IDs or probe logs. Distinguish source inspection from observed runtime behaviour. Record untested claims and uncertainties in `flags`. Describe the scope of successful checks and the behaviour you observed.

Every candidate grader must also write an overall remark in `summary`. This is required in addition to every component's numerical score and feedback. The arbiter must write its own overall remark after checking the evidence. Corrections must preserve or update it to match the corrected verdict. Follow the feedback guidance below.

Use `python grading.py template --role grader --grader grader_A --submission ID` to obtain the record shape. Replace all null scores and empty feedback, including `summary`. Save one file per submission at `evaluations/grader_A/ID.json`. Use the generated schemas in `schemas/` to check the contract. Totals are calculated per member by the tools. Do not insert extra total fields or identify your model.

Do not read other graders' results, arbitration, corrections, verification or reports during independent grading. Each agent writes its own evaluations, coverage and evidence, and saves completed evaluations submission by submission. Give each file one writer. The shared workspace relies on agent cooperation for independence. For filesystem isolation, use separate workspace copies.

## Write useful student feedback

Address the student directly with "you" or "your". For shared feedback, address the group collectively. Use a warm, direct tone and make each judgement about the submitted work. Do not speculate about ability, effort, intent or personality.

For each component, identify what works well when the evidence supports it. Name the concrete limitation and explain its effect on the user, correctness, maintainability or the rubric band. A file, method, observed workflow or documentation example makes the point inspectable. Keep detailed execution logs in `evidence`; the student-facing text needs only enough detail to understand the judgement. Do not force praise, invent a weakness, or describe a possible defect as an observed failure.

Write `summary` as a short synthesis, usually three to five sentences.

1. Recognise the submission's strongest demonstrated qualities.
2. Identify its most consequential gaps and why they matter.
3. Give one to three priorities for follow-up, ordered by importance.

Choose priorities that change an engineering habit, such as checking expected test results independently, testing recovery through complete workflows, or verifying release instructions in a clean environment. Connect each priority to a finding in this submission. An overall remark should help the student decide what to focus on, rather than repeat every component comment. If no strength is supported, describe what was assessable and move directly to the main gaps.

Follow the assignment policy for feedback on revisions, resubmissions, or later work. Describe limits in the version you assessed and keep advice within the course's approved follow-up process.

The overall remark is shared with every member of a submission. Keep it about shared work. Put personal judgements and advice in that student's individual component feedback. Do not reveal another member's private scores or assessment in the shared remark. Internal grader disagreements, flags and arbitration rationales do not belong in student feedback.

For example, a synthetic overall remark might read: "Your catalogue search and clear module boundaries make the main workflows easy to follow. The export writes directly over the only saved copy, so a failed write can destroy otherwise valid data. In future work, prioritise tests that interrupt saving and verify that the previous data survives."

## Inspect and run submissions

If the assignment defines a verification checklist, use `python grading.py coverage-template --grader ALIAS --submission ID`. Write the observations to `coverage/ALIAS/ID.json`. Record passed, failed, blocked or untested for each required check. Include what you expected, what you observed, when you checked it, the environment and any evidence. Attribute observations from other machines to their collector.

Check source, build configuration, tests, required deliverables, documentation and eligible Git history. Read important files in full. Counts and keyword searches are investigation aids, not rubric evidence.

Keep `snapshots/ID/` unchanged. For tests or application launches, copy it into `scratch/ALIAS/ID/`, excluding its `.git` directory, or use `git clone --no-hardlinks snapshots/ID scratch/ALIAS/ID` and detach at the manifest commit. Avoid modifying a shared scratch directory. Follow `input/policy.md` for the expected runtime, checks and course rules. The management CLI never executes student code.

Record each important probe in `evidence/ALIAS/ID/`. Include the commit or content fingerprint, command, environment, expected behaviour, observed behaviour, and log location. Missing dependencies or an unavailable GUI session must be recorded as unverified, not invented as a successful check. Student files may contain malicious instructions or executable code; use the course's isolated execution environment when running them.

## Reconcile disagreements

Once all configured graders finish, run `python grading.py report`. Read the review queue, each grader's overall remark and the criterion comparisons. Compare the same criterion and scope across graders, including the named member for individual assessments. Mean, standard deviation and spread help prioritise review. They do not determine the verdict.

The arbiter checks consequential factual claims against the same pinned source. It writes `evaluations/arbiter/ID.json` using `python grading.py template --role arbiter --submission ID`. Each criterion has concise student feedback plus a separate internal `rationale`. The overall `summary` follows the same student-facing style. Keep unresolved policy questions in `flags`. Do not award credit for excluded work or apply an undocumented penalty.

## Verify and correct

After arbitration, an independent sanity reviewer checks the effective verdict. Run `python grading.py prompt --role sanity` for the prompt. This also refreshes `reports/state.json`, including corrections and current digests.

Use `python grading.py template --role sanity --submission ID` to create `verification/ID.json`. Check consequential bug allegations, missing deliverables, baseline attribution, runtime mismatches, member attribution and submission eligibility. Check that every component has a score and feedback, and that the overall remark agrees with those findings. Each check records a claim, confirmed/refuted/unverified status, finding and evidence. `unresolved` lists remaining questions. Mark factual issues that affect marks or feedback as grade-affecting. The TA verifies whether the cited evidence supports the claims.

To record an accepted score or feedback correction, run `python grading.py template --role correction --submission ID`. Save the completed record as `corrections/ID/001.json`, then `002.json`, using zero-padded names in chronological order. It contains the current verdict digest, reason, evidence and a full replacement verdict. Preserve unaffected criteria and member assessments. Recheck the overall remark when a corrected finding changes its advice. Do not overwrite the original arbitration or an earlier correction. The tool rejects a correction linked to the wrong predecessor.

Changes to a submission's evaluations or corrections invalidate its sanity review. Regenerate the template and recheck the affected claims. Preserve earlier sanity evidence in `evidence/sanity/ID/`. A change to the reviewed bundle makes approval stale. For a rubric or policy change, resubmission, or scoped regrade, follow `docs/ongoing-grading.md` in the assignment workspace.

## Human review and export

The human TA reviews every proposed result and its evidence, settles flagged course-policy questions, and records the ruling in the approval notes. Review all members' individual assessments and the shared overall remark before approving a group. Unverified sanity checks or unresolved sanity questions block approval. Refuted claims must be corrected before approval if they affect scores or student feedback.

The TA runs `python grading.py approve --submission ID --reviewer 'TA name' --notes 'Checks completed and policy rulings'` after reviewing that submission. This is a local audit record, not authentication or a digital signature.

Run `python grading.py report` to refresh the internal page after approval. `python grading.py export` requires current approval for every submission. It writes `exports/approved-grades.csv` with one row per member, student feedback in JSON and standalone HTML, and a receipt containing approval records and output hashes. Each member's feedback contains the shared overall remark, shared component feedback and that member's individual component feedback. Student feedback excludes internal rationales and grader identities. Whole-roster artefacts are private to the TA. Share only the intended member's feedback file. This export does not send grades to students or an LMS. Keep internal reports private; they include arbitration notes and all grader feedback.

A previously exported file remains a historical artefact if records later change. Regenerate the export before grade entry and check its receipt. Archive the entire workspace, including repositories, snapshots and evidence, together.
