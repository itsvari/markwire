# Set up an assignment in Markwire

Run `python grading.py setup` for the terminal wizard. It uses the files described below, asks about verification and agent preferences, and shows a worked grade before confirming the policy.

To use a coding agent, open the new workspace and ask it to follow this page. Give it the official course material and roster. Have it draft the normalised files, ask you to resolve ambiguous scoring and policy rules, and confirm the tool and model preferences in the next section. Run `python grading.py setup --review` to validate the draft and show the worked grade. The TA confirms the policy before the agent sets `policy_confirmed` to `true` and runs `prepare`.

1. Put the official assignment material in `input/brief.md` and the marking guidance in `input/rubric.md`. If the source uses another format, retain it in `input/` and normalise its criterion anchors into the Markdown rubric.
2. Edit `assignment.json`. Set the assignment ID, title, anonymous grader aliases, criteria, weights, scopes and review thresholds. Give each criterion a stable `key`, an integer `max_score`, and a reference to its rubric anchors. The marks default to integers. For half marks or discrete levels, add a sorted `allowed_scores` list that includes the maximum. Weights are relative shares normalised to 100 percent. `rounding_digits` sets the final percentage precision and defaults to one. Use `review_thresholds.weighted_percentage_points` to identify significant weighted disagreements. For each criterion, choose `submission` for a shared assessment or `individual` for a separate assessment per member. Leave `unresolved` until the TA decides.
3. Complete `input/policy.md`. Confirm submission selection, late-work handling, runtime, required deliverables, baseline attribution and any domain restrictions. Define acceptable evidence for individual criteria and how to resolve missing or disputed attribution. The generic example does not establish course policy.
4. Put the pristine starter files in `input/baseline/`, or set `baseline_required` to false if no starter exists. Exclude its `.git` directory when copying. The manifest fingerprints the actual baseline files.
5. Add the roster to `input/roster.csv`. Use one row per student with columns `submission_id,student_id,name,github_username,repo_url,branch,commit`. For a group, repeat the same submission ID and source fields for each member. Give each member a unique student ID. Provide GitHub usernames when relevant. IDs must use letters, digits, underscores or hyphens.
6. If importing a roster, map its submission and student columns with `import-roster`, as shown below. Copy optional diagnostics into `input/diagnostics/`.
7. Agree on the common verification checklist, agent settings and machine preferences. Review the workspace and resolve every criterion scope. Set `policy_confirmed` to `true` after the TA confirms the documents and configuration, then run `python grading.py prepare`.

Preparation requires Python 3.11 or newer. Git submissions also require Git and use your existing Git authentication. This tool does not install Java, build tools or model clients.

## Set verification and run preferences

Use `input/verification.json` for the common verification floor. Define the required checks before freezing the assignment. A check can name associated criterion IDs and capabilities. An empty criterion list applies across the assignment. Each grader records their own observation, including the environment, role, expected and observed behaviour, status and evidence.

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

Ask the TA about the following when drafting `private/run-preferences.json`. The terminal wizard writes the same file. Keep this operator-only file out of shared bundles and repositories. Use authorised local sessions or the harness's secret flow for credentials.

| Preference | Choices |
| --- | --- |
| `orchestration` | `manual` or `t3` |
| `models` | Model selections keyed by grader alias, `arbiter`, `sanity` or `report`. A selection contains `provider`, `model` and optional `options` with string or boolean values. |
| `roles` | Tool and subagent permissions keyed by role. `grader` supplies the default for grader aliases. A specific alias takes precedence. |
| `reports` | `off`, `blockers`, or `selected`. For `selected`, list the desired stages from `setup`, `grading`, `arbitration`, `sanity` and `release`. |
| `machines` | An alias, capability IDs such as `browser` or `human-auth`, and an optional T3 environment ID. Use `null` for a machine reached manually. |

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

Use `subagents: "off"` with both worker counts at zero. Use `"required"` with a positive minimum per submission and a concurrency cap. Specialist `task_models` can use `evidence`, `vision`, `feedback_draft` or `feedback_edit`. Authentication can be `off`, `human` or `preauthorised` as appropriate to the available browser or desktop access. If a required worker, tool or sign-in is unavailable, mark the affected work blocked and report the reason.

## Attribute submissions to students

Use the same `submission_id` for members who submitted one repository together. An individual submission has one roster row. Detailed reports show each student's name and GitHub username when provided. Record only the student identifiers your grading process uses. A team repository URL does not establish an individual's GitHub username.

For example, a fictional two-person submission uses two rows:

```csv
submission_id,student_id,name,github_username,repo_url,branch,commit
team_01,s001,Example Student One,example-one,https://example.invalid/submissions/team-01.git,main,
team_01,s002,Example Student Two,example-two,https://example.invalid/submissions/team-01.git,main,
```

To import a CSV with different headers, supply the corresponding column names. The example assumes `roster.csv` is in the workspace.

```text
python grading.py import-roster --csv roster.csv --submission-column "Team ID" --student-column "Student ID" --github-column "GitHub username" --name-column "Name" --repo-column "Repo URL" --branch-column "Branch"
```

Add `--commit-column "Commit"` for explicit commit selection. For individual-only assignments, the same source column can supply both submission and student IDs. Omit `--github-column` when the roster has no GitHub IDs. Prepare one row per member before importing.

Set criterion scopes independently of group size. A solo submission can still have individual criteria. A shared criterion gives every member the same score and feedback, while an individual criterion gives each member their own. The tools combine shared and personal scores into each member's weighted result. They do not decide contribution proportions or infer effort from Git history. Preparation refuses unresolved scopes.

## Select the submitted source

For a local directory or ZIP, put its path in `repo_url` and leave both `branch` and `commit` blank. Relative paths resolve from the assignment workspace. `prepare` materialises a content-pinned snapshot.

`explicit` mode requires a full submitted commit SHA in every roster row. It verifies that the commit belongs to the submitted branch. This is preferred when the course supplies a submission record.

`deadline` mode requires an ISO timestamp with a timezone offset, for example `2030-06-01T17:00:00+00:00`. `boundary` selects strictly before or at-or-before. `timestamp` selects committer time or the later of author and committer times. The tool walks the branch's first-parent history in ancestry order and selects the first eligible commit. It examines all timestamps, even when they are out of order. Merged side-branch content follows the selected merge tree. Git timestamps are student-controlled metadata and do not prove when work was uploaded. Document whether this approximation is accepted by the course.

`head` mode freezes the submitted branch head at preparation time. It applies no deadline filtering. Use it only when this matches the course's submission policy.

Leave the roster's `commit` field empty in head and deadline modes. There is no automatic fallback between modes. Missing repositories, unavailable eligible commits or unsupported submodules stop preparation. Resolve the submission case with the teaching team rather than silently assigning zero or using current HEAD.

Preparation fetches repositories into `repositories/` and creates independent detached clones in `snapshots/`. A successful rerun checks the existing manifest and snapshots without refreshing them. If a run fails partway through, correct a transient fetch problem and rerun. A changed partially prepared snapshot requires a new workspace. Do not update frozen inputs or tool files in place.

## Start the agents

Run these commands and paste each resulting prompt into your chosen coding agent with this workspace open:

```sh
python grading.py prompt --role grader --grader grader_A
python grading.py prompt --role grader --grader grader_B
python grading.py prompt --role grader --grader grader_C
```

Use only configured aliases. Add `--submission ID` to grade one submission and all its members. In manual mode, use your configured models when opening each agent.

Every candidate grader supplies component scores and feedback, plus an overall remark in `summary`. The arbiter writes a separate overall remark after resolving the evidence. The manual defines the feedback style, including strengths, improvements and priorities for follow-up under the assignment policy.

After the independent results arrive:

```sh
python grading.py validate
python grading.py report
python grading.py prompt --role arbiter
# Run the arbiter, then:
python grading.py prompt --role sanity
# Run the sanity reviewer, settle findings, then:
python grading.py validate
python grading.py report
```

Open `reports/review.html` for the Columns review queue. Select a member and a rubric row to compare scores, feedback and evidence. Use the Overall remarks and TA review tabs for summaries, sanity checks and corrections. Raw totals and weighted percentages are shown separately. The self-contained HTML opens offline with JavaScript enabled; its controls are read-only. `reports/comparison.csv` supports spreadsheet analysis. Follow the human-review procedure in `GRADING_MANUAL.md` before exporting. Review every member's personal assessment before approving the submission.
