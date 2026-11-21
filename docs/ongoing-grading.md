# Revise grades, run remote checks, and return stage decisions

## Revise a prepared assignment

Because prepared workspaces freeze their inputs and snapshots, any post-preparation change to the rubric, policy, verification checklist, or submissions happens in a sibling revision workspace.

Save the requested changes in a JSON file. To add, remove, or modify rubric criteria, supply a full replacement `assignment.json` alongside any updated input files. Relative paths resolve from the changes file's directory.

```json
{
    "reason": "Add the testing criterion",
    "assignment": "assignment-v2.json",
    "inputs": {"rubric.md": "rubric-v2.md"},
    "changed_criteria": []
}
```

When `rubric.md` changes, list every criterion whose scoring anchors changed semantically under `changed_criteria`. An explicit empty list (`[]`) confirms that the rubric prose introduces no semantic changes beyond the proposed `assignment.json`. Use the same replacement configuration mechanism to alter weights, score ladders, rounding, or scopes, or to retire criteria.

To replace a submission, set `resubmissions` to an object keyed by submission ID: use a full 40-character Git commit SHA for a Git submission, or `{"source": "replacement.zip"}` for a replacement local directory or ZIP archive. To reassess an unchanged submission, set `regrades` to a list such as `[{"submission_id": "team-a", "criteria": ["correctness"]}]`, adding `"member": "s001"` to target individual criteria for one member.

1. Run `python grading.py revise plan changes.json` and inspect the reassessment, carry-forward, and review scopes.
2. Run `python grading.py revise create changes.json ../assignment-v2 --confirm PLAN_DIGEST`, using the `plan_digest` printed by `revise plan`.
3. Open the new revision workspace. Run `python grading.py status` and use `python grading.py prompt --role ROLE --submission ID` to launch only the roles with outstanding work. Generated templates prefill carried assessments; graders fill only entries with `null` scores and update overall remarks or flags where needed.
4. Reconcile and verify the changed verdicts, record current human approvals, and run `python grading.py export --amendment` to export only the affected submissions under `exports/amendment/`.

If an older workspace carries an earlier copy of the engine, run `revise` from the Markwire repository and point `--workspace PATH` at the parent workspace. The parent workspace keeps its original engine, records, and historical exports untouched.

## Send a remote check

Use detached task bundles when a check needs capabilities on another machine (such as a specific operating system, browser session, or human-assisted sign-in) without copying the whole grading workspace.

Write a task specification JSON for one submission. Name the task, declare required capabilities, and list the workspace files the worker needs. Omit `include_snapshot` for hosted checks that need only documentation or fixtures; set it to `true` when the worker needs the pinned submission tree.

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

1. On the main machine, run `python grading.py bundle pack probe.json probe.zip`. Transfer `probe.zip` and `grading_bundles.py` to the remote machine over your authorised channel.
2. On the remote machine, run `python grading_bundles.py open probe.zip task-workspace`. Open the assigned agent in `task-workspace`, point it at `TASK.md`, and handle any interactive sign-in locally.
3. Run `python grading_bundles.py finish task-workspace result.zip` and transfer `result.zip` back to the main machine.
4. On the main machine, run `python grading.py bundle import result.zip`. Inspect the verified observations and cited evidence under `remote_tasks/TASK_ID/ATTEMPT/result/`.

To collect a grading artefact from a remote worker, declare its path under `outputs` (for example, `["outputs/assessment.json"]`). The importing grader reviews the returned artefact before recording an evaluation through the standard workflow. Use a new attempt number for follow-up checks after a partial or blocked return. For optional T3 Code dispatch to linked machines, see the [T3 orchestration runbook](t3-orchestration.md).

## Return decisions from a stage report

Stage reports give human reviewers a single offline page to inspect open findings for a stage, choose follow-up actions, and hand a validated decision packet back to the grading CLI.

### Remediation packet contract

Each stage report uses a single, report-wide decision packet:

- **One complete packet per stage report.** The packet includes exactly one choice for every open finding that offers options across all topics.
- **No partial packets.** Filtering or searching the list narrows what you see, not what the packet requires. **Show JSON**, **Copy JSON**, and **Download JSON** stay disabled until every open finding with options has a valid answer, and `import-decisions` rejects partial packets.
- **Defer is an explicit choice.** Wherever **Defer** is offered, selecting it counts as a completed choice and records that the finding stays open for later attention without holding up other actions.
- **Custom actions require instructions.** Selecting the custom choice (**Specify an action**) requires written instructions in the notes field before the finding counts as answered.
- **Reconciled and informational findings need no choice.** Findings without options are shown for context and are excluded from the packet.
- **Bound to the current report.** Every packet carries the report's `runId`, `stage`, and `reviewDigest`. If underlying evaluations, coverage, or verification records change, regenerate the stage report before importing decisions.

### Reviewer workflow

1. Generate the stage report:

   ```text
   python grading.py stage-report --stage arbitration
   ```

   Choose `setup`, `grading`, `arbitration`, `sanity`, or `release` for `--stage`.
2. *(Optional)* To have an agent synthesise related findings before review, export the draft JSON with `python grading.py stage-report --stage STAGE --draft` and ask the agent to write a curation file (format below). Regenerate the report with `python grading.py stage-report --stage STAGE --curation curation.json`.
3. Open `reports/STAGE.html` in a browser (it runs offline and sends nothing over the network):
   - Triage **Blockers** and **Grade impact** findings first; the queue orders open findings by severity and weighted impact.
   - Inspect each finding's summary, suggested next step, and linked evidence sources.
   - Choose an option for each open finding, adding optional context or required custom instructions. Use **Defer** where offered for items you do not wish to act on yet.
   - Use **Next unanswered** to jump straight to remaining open findings (it clears active search or filters automatically if a remaining item is hidden).
4. Once every open finding with options is answered, click **Show JSON** to inspect the packet if desired, then click **Copy JSON** or **Download JSON** to save it (for example, as `decisions.json`).
5. Import the packet via the CLI:

   ```text
   python grading.py import-decisions decisions.json --reviewer "TA Name"
   ```

   The CLI validates completeness, option IDs, custom instructions, and report freshness, then writes a receipt under `decisions/` mapping each decision to its owning role. Hand that receipt to the agent responsible for the next action, and re-check any verdict affected by remediation before recording human approval.

### Curation file format

A curation file binds to a specific draft through `source_digest`. Each group merges one or more findings from the same submission and status without lowering their severity:

```json
{
    "source_digest": "DIGEST_FROM_DRAFT",
    "groups": [
        {
            "finding_ids": ["FINDING_FROM_DRAFT", "ANOTHER_FINDING_FROM_DRAFT"],
            "topic": "Hosted service",
            "title": "Hosted request returned 503",
            "summary": "Two graders observed the same hosted service failure.",
            "next_step": "Repeat the check under the course outage policy."
        }
    ]
}
```
