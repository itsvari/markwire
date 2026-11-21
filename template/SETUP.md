# Set up an assignment in Markwire

Run `python grading.py setup` for the interactive terminal wizard. It guides you through the configuration files below, asks about verification checks and agent preferences, and displays a worked example grade before confirming the policy.

To configure the workspace with a coding agent, open the newly initialised directory and ask the agent to follow this guide using your official course materials and roster. Have it draft the normalised files, ask you to resolve any ambiguous scoring or policy rules, and confirm the tool, model, and stage-report settings below. Run `python grading.py setup --review` to validate the draft and inspect the worked grade. Only after the TA confirms the policy should `policy_confirmed` be set to `true` and `prepare` be run.

1. Place the official assignment brief in `input/brief.md` and the marking guidance in `input/rubric.md`. If your source uses another file format, keep the original under `input/` and normalise its criterion anchors into `input/rubric.md`.
2. Edit `assignment.json`. Set `assignment_id`, `title`, anonymous grader aliases, criteria, weights, scopes, and review thresholds. Give each criterion a stable `key`, a positive integer `max_score`, and a pointer to its rubric anchors in `guidance`. Marks default to whole integers from `0` to `max_score`; for half marks or discrete bands, add an ascending `allowed_scores` list that includes `max_score`. Weights are relative shares normalised to 100 per cent. `rounding_digits` sets final percentage precision (default: `1`). Use `review_thresholds.weighted_percentage_points` to flag consequential weighted disagreements in stage reports. Set each criterion's `scope` to `submission` (shared) or `individual` (per member); leave `unresolved` until the TA decides.
3. Complete `input/policy.md`. Document source selection, late-work handling, runtime expectations, required deliverables, starter baseline attribution, and any domain restrictions. Specify acceptable evidence for individual criteria and how to handle missing or disputed attribution. The starter placeholder does not establish course policy.
4. Copy pristine starter files into `input/baseline/` (excluding any `.git` directory), or set `baseline_required` to `false` if there is no starter material. `manifest.json` fingerprints the actual baseline files.
5. Populate `input/roster.csv` with one row per student and columns `submission_id,student_id,name,github_username,repo_url,branch,commit`. For groups, repeat the same `submission_id` and source columns for every member. Give every student a unique `student_id`, and supply `github_username` (without `@`) where relevant. IDs may contain only ASCII letters, digits, underscores, and hyphens.
6. To normalise a roster exported from another system, use `import-roster` as shown below. Place optional first-pass diagnostics in `input/diagnostics/`.
7. Agree on any common verification checklist, agent settings, stage-report triggers, and remote machines. Confirm every criterion scope, set `policy_confirmed` to `true` once the TA approves the setup, and run `python grading.py prepare`.

Preparation requires Python 3.11 or later. Git submissions also require Git and rely on your existing Git credentials. Markwire does not install compilers, runtimes, build tools, or model clients.

## Set verification and run preferences

Use `input/verification.json` to define a shared verification checklist before freezing the workspace. Each check may target specific criterion keys (an empty list applies across the whole assignment) and declare required capabilities. Every grader independently records their environment, role, expected and observed behaviour, status, and evidence.

```json
{
  "version": 1,
  "checks": [
    {
      "id": "core-flow",
      "instructions": "Open the submitted app, sign in with the lab account and complete the core workflow.",
      "criteria": ["correctness"],
      "capabilities": ["browser", "human-auth"],
      "required": true
    }
  ]
}
```

Confirm the following settings with the TA when drafting `private/run-preferences.json` (the terminal wizard writes the same schema). Keep this operator-only file out of shared bundles and repositories, and handle credentials through authorised local sessions or your agent harness's secret flow.

| Preference | Choices |
| --- | --- |
| `orchestration` | `manual` or `t3` |
| `models` | Model selections keyed by grader alias, `arbiter`, `sanity`, or `report`. Each entry specifies `provider`, `model`, and optional `options` mapping names to strings or booleans. |
| `roles` | Tool and subagent permissions keyed by role. `grader` sets the default for all grader aliases; an alias-specific entry overrides it. |
| `reports` | Stage report triggers: `off`, `blockers`, or `selected`. For `selected`, list stages from `setup`, `grading`, `arbitration`, `sanity`, and `release`. |
| `machines` | Remote machine aliases with capability IDs (such as `browser` or `human-auth`) and an optional T3 `environment_id` (`null` for manual bundle transfer). |

```json
{
  "version": 1,
  "orchestration": "manual",
  "models": {},
  "roles": {
    "grader": {
      "subagents": "as_needed",
      "minimum_workers_per_submission": 0,
      "max_concurrent_workers": 2,
      "task_models": {},
      "browser": true,
      "desktop": false,
      "auth": "human"
    }
  },
  "reports": {"mode": "selected", "stages": ["grading", "arbitration", "sanity"]},
  "machines": {"test-worker": {"environment_id": null, "capabilities": ["browser", "human-auth"]}}
}
```

Use `"subagents": "off"` with both worker counts at `0`, or `"required"` with a positive minimum per submission and concurrency cap. Specialist `task_models` accept `evidence`, `vision`, `feedback_draft`, and `feedback_edit`. `auth` accepts `off`, `human`, or `preauthorised` when browser or desktop access is enabled. If a required worker, tool, or sign-in is unavailable, mark the affected check blocked and flag the reason.

## Attribute submissions to students

Members submitting one repository or archive together share the same `submission_id`; solo submissions have a single row. Reports display each student's name and optional GitHub handle. Record only the identifiers your marking process requires—a team repository URL never establishes an individual's GitHub username.

For example, a fictional two-student group uses two rows:

```csv
submission_id,student_id,name,github_username,repo_url,branch,commit
team_01,s001,Example Student One,example-one,https://example.invalid/submissions/team-01.git,main,
team_01,s002,Example Student Two,example-two,https://example.invalid/submissions/team-01.git,main,
```

To import a CSV with different column headers, map them with `import-roster` (assuming `roster.csv` is in the workspace):

```text
python grading.py import-roster --csv roster.csv --submission-column "Team ID" --student-column "Student ID" --github-column "GitHub username" --name-column "Name" --repo-column "Repo URL" --branch-column "Branch"
```

Add `--commit-column "Commit"` when using explicit commit selection. For solo-only assignments, `--submission-column` and `--student-column` may name the same header. Omit `--github-column` if your roster does not track GitHub usernames. Ensure the source CSV has one row per student before importing.

Criterion scopes are independent of group size: a solo submission can still include `individual` criteria. Shared criteria award every member the same score and feedback; individual criteria record separate scores and feedback per student. Markwire combines shared and individual criteria into each student's totals without inferring effort or contribution splits from Git history. `prepare` refuses unresolved scopes.

## Select the submitted source

For a local directory or ZIP archive, put its path in `repo_url` and leave both `branch` and `commit` blank. Relative paths resolve from the assignment workspace, and `prepare` creates a content-fingerprinted snapshot.

- `explicit` mode requires a full 40-character commit SHA in every Git roster row and verifies that the commit lies on the submitted branch. Prefer this mode whenever your course records submitted commits.
- `deadline` mode requires an ISO 8601 cut-off timestamp with a time zone offset, such as `2030-06-01T17:00:00+00:00`. `boundary` chooses `exclusive` (strictly before) or `inclusive` (at or before). `timestamp` checks either `committer` time or `both` (the later of author and committer times). Markwire walks first-parent ancestry and picks the latest eligible commit, inspecting all timestamps even when out of order; merged side-branch content follows the selected merge tree. Because Git timestamps are student-controlled metadata and do not prove upload time, record in `input/policy.md` whether the course accepts this rule.
- `head` mode pins the submitted branch head at preparation time with no deadline filter. Use it only when this reflects course policy.

Leave `commit` blank in `head` and `deadline` modes. There is no silent fallback between modes: missing repositories, absent eligible commits, or unmaterialised submodules stop preparation so the teaching team can record an explicit ruling instead of silently assigning zero or taking `HEAD`.

`prepare` caches bare repositories in `repositories/` and creates detached clones in `snapshots/`. Re-running `prepare` on a prepared workspace verifies the manifest and snapshots without re-fetching. If an initial run fails partway through on a transient network error, resolve the fault and re-run; if a partially prepared snapshot differs on retry, start a clean workspace. Never edit frozen inputs or engine files in place.

## Start the agents

Print each independent grader's prompt and paste it into your chosen coding agent opened in this workspace:

```text
python grading.py prompt --role grader --grader grader_A
python grading.py prompt --role grader --grader grader_B
python grading.py prompt --role grader --grader grader_C
```

Use only configured grader aliases, and add `--submission ID` to scope work to one submission and all its members. In `manual` mode, select your chosen model when opening each agent.

Every grader supplies component scores, feedback, evidence, and an overall remark in `summary`. The arbiter writes its own overall remark after reconciling evidence. See `GRADING_MANUAL.md` for student feedback guidance.

Once independent grading finishes:

```text
python grading.py validate
python grading.py report
python grading.py prompt --role arbiter
```

After the arbiter finishes:

```text
python grading.py prompt --role sanity
```

After the sanity reviewer finishes and any findings are settled:

```text
python grading.py validate
python grading.py report
```

Open `reports/review.html` in a browser for the Columns staff ledger. Select a submission, focus on a student or all members, and inspect criterion scores, feedback, evidence, and rationales alongside the **Overall remarks** and **TA review** tabs. Raw totals and weighted percentages appear separately, and all browser controls are strictly read-only. `reports/comparison.csv` supports spreadsheet analysis.

To review stage-level findings and return remediation decisions as a single complete JSON packet, or to plan revisions and remote checks after preparation, see `docs/ongoing-grading.md` in the workspace. Follow the human review and export steps in `GRADING_MANUAL.md`, checking every member's individual assessments before approving a submission.
