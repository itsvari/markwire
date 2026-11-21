# Markwire

Markwire helps teaching teams grade coding assignments with independent AI-assisted reviews. Graders gather evidence against a shared rubric, an arbiter reconciles their assessments, an independent sanity reviewer verifies the proposed verdict, and a human reviewer, such as a teaching assistant (TA) or instructor, approves every result before export.

Markwire supports Git repositories, local directories, and ZIP archives. Rubrics can combine shared submission criteria with individual member criteria. Every workspace records which submission snapshot was assessed, which evidence supported each verdict, and which corrections, decisions, and approvals followed.

## Try the demo

Ensure Python 3.11 or later is available as `python` in your terminal, and install Git so the demo can create local repositories. Run these commands from the Markwire checkout, choosing a fresh destination path if `../markwire-demo` already exists.

```text
python examples/run_demo.py ../markwire-demo
python ../markwire-demo/grading.py --workspace ../markwire-demo stage-report --stage grading
```

Open these generated files in a browser to explore the offline reports:

- `../markwire-demo/reports/review.html` for the Columns staff ledger
- `../markwire-demo/reports/grading.html` for the grading stage report and remediation hand-off
- `../markwire-demo/exports/student-feedback.html` for the approved feedback archive

The demo uses two fictional Git submissions and synthetic fixture assessments. It runs completely offline with no model account.

## Set up an assignment

Start from the Markwire checkout to initialise a self-contained assignment workspace:

```text
python grading.py init ../grading-assignment
cd ../grading-assignment
python grading.py setup
python grading.py prepare
```

Provide the assignment brief, rubric, roster, and any starter files. Use the interactive setup wizard or follow [SETUP.md](template/SETUP.md) to configure the workspace, then confirm the course policy before preparing pinned submission snapshots. All configuration, evidence, and grading records stay inside the assignment workspace.

Launch each independent grader with the prompt for its configured alias:

```text
python grading.py prompt --role grader --grader grader_A
```

Open each coding agent in the assignment workspace and give it its generated prompt. Use `python grading.py status` to track progress and `python grading.py report` to refresh the Columns staff ledger. Once all configured graders finish, run the arbiter and sanity reviewer, record human approval, and export the final gradebook and student feedback. See the [setup guide](template/SETUP.md) and [grading manual](template/GRADING_MANUAL.md) for the complete procedure.

## Review, remediate, and revise

The offline Columns staff ledger compares candidate scores, feedback, evidence, and arbitration rationales across criteria and group members.

Optional stage reports (`stage-report --stage STAGE`) group open findings across topics so a reviewer can decide follow-up actions in one pass. Each stage report produces a single remediation packet covering every open finding with options across all topics: **Defer** counts as a completed choice where offered, custom choices require written instructions, and copying or downloading the JSON packet stays disabled until every open finding is answered. Importing the packet with `import-decisions` validates it against the current report and rejects partial or stale packets.

See the [revision, remote-check, and decision guide](docs/ongoing-grading.md) for the stage decision workflow, targeted revisions to prepared assignments, and detached remote checks.

Markwire works with any coding agent that can read and write workspace files. For optional model dispatch and linked-machine coordination in T3 Code, see the [T3 orchestration runbook](docs/t3-orchestration.md).

Keep real grading workspaces outside the Markwire checkout. Each workspace embeds the engine files it was prepared with, and any change to frozen inputs requires a sibling revision. See the [configuration and file reference](docs/reference.md) for file layouts, record schemas, and CLI commands.

## Run the checks

From the Markwire checkout, run the Python test suite (Git must be installed so tests can create local fixture repositories). To verify the interactive staff ledger and stage report hand-off logic, install Node.js and run the two report checks after generating the demo and its grading stage report:

```text
python -m unittest discover -s tests -v
node tests/validate_report.js ../markwire-demo/reports/review.html
node tests/validate_stage_report.js ../markwire-demo/reports/grading.html
```
