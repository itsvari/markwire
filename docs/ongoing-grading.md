# Revise grades, send remote checks, and review decisions

## Revise a prepared assignment

Save the requested changes in a JSON file. For a rubric addition, supply a complete replacement assignment configuration and the revised rubric document. Paths are relative to the changes file.

```json
{
    "reason": "Add the testing criterion",
    "assignment": "assignment-v2.json",
    "inputs": {"rubric.md": "rubric-v2.md"},
    "changed_criteria": []
}
```

List any criteria whose anchors changed in the rubric prose under `changed_criteria`. An explicit empty list confirms that the prose adds no semantic changes beyond the proposed configuration. Use the same configuration file mechanism for weight, scale, or scope changes and criterion removal.

To replace a submission, set `resubmissions` to an object keyed by submission ID. Use a full Git commit or an object such as `{"source": "replacement.zip"}` for a replacement local directory or ZIP. To reassess an unchanged submission, set `regrades` to a list such as `[{"submission_id": "team-a", "criteria": ["correctness"]}]`. Add `"member": "s001"` to target individual criteria for one member.

1. Run `python grading.py revise plan changes.json`. Inspect the reassessment, carry-forward, and review scopes.
2. Run `python grading.py revise create changes.json ../assignment-v2 --confirm PLAN_DIGEST`. Use the `plan_digest` printed by the plan.
3. Open the new workspace. Run `python grading.py status` and use `prompt --submission ID` to launch the roles with outstanding work. Templates prefill carried marks. Graders fill the pending assessments and update the overall remarks as needed.
4. Reconcile and verify the changed verdicts. Record the TA's current approvals, then run `python grading.py export --amendment` for the affected submissions. The new workspace writes the amendment to `exports/amendment/`.

For a workspace with an older copied engine, launch `revise` from the Markwire repository and select the parent with `--workspace PATH`. The parent retains its original engine, records and issued exports.

## Send a remote check

Write a task specification for a single submission. Name the check, select the required capabilities, and list the files the worker needs. Omit `include_snapshot` for hosted checks that need only documentation or fixtures. Set it to `true` when the whole pinned source tree is required.

```json
{
    "task_id": "team-a-browser",
    "attempt": 1,
    "submission_id": "team-a",
    "capabilities": ["browser", "human-auth"],
    "files": ["input/policy.md"],
    "checks": [
        {"id": "member-login", "instructions": "Use the assigned test account and record the outcome of the member login."}
    ]
}
```

1. On the main machine, run `python grading.py bundle pack probe.json probe.zip`. Send `probe.zip` and Markwire's `grading_bundles.py` to the worker using the authorised transfer channel.
2. On the worker, run `python grading_bundles.py open probe.zip task-workspace`. Start the assigned agent in `task-workspace` and point it at `TASK.md`. Handle sign-in locally.
3. Run `python grading_bundles.py finish task-workspace result.zip`. Return the result archive.
4. On the main machine, run `python grading.py bundle import result.zip`. Review the imported observations in `remote_tasks/TASK_ID/ATTEMPT/result/`.

To collect a grading artefact, list its path in `outputs`, for example `["outputs/assessment.json"]`. The importing grader reviews it before saving an assessment through the normal grading workflow. Use a new attempt number for follow-up work after a partial result. For T3 dispatch, follow the [T3 runbook](t3-orchestration.md).

## Return decisions from a stage guide

1. Run `python grading.py stage-report --stage arbitration`. Choose `setup`, `grading`, `arbitration`, `sanity`, or `release` to inspect another stage.
2. To have an agent synthesise related findings, give it the JSON from `stage-report --stage STAGE --draft` and ask it to create a curation file. Regenerate the guide with `stage-report --stage STAGE --curation FILE`.
3. Open the guide, inspect the evidence and select the remediation choices. Copy or download the decision JSON.
4. Run `python grading.py import-decisions decisions.json --reviewer NAME`. Give the receipt to the agent responsible for the next action. Recheck any verdict affected by a remediation before recording approval.

The curation file identifies the draft by its `source_digest`. A group cites the original findings that describe the same issue.

```json
{
    "source_digest": "DIGEST_FROM_DRAFT",
    "groups": [
        {
            "finding_ids": ["FINDING_FROM_DRAFT", "ANOTHER_FINDING_FROM_DRAFT"],
            "topic": "Hosted AI",
            "title": "Hosted request returned 503",
            "summary": "Two graders observed the same hosted failure.",
            "next_step": "Repeat the check under the course outage policy."
        }
    ]
}
```
