# Markwire

Markwire helps teaching teams grade coding assignments with independent AI-assisted reviews. Graders collect evidence against a shared rubric. An arbiter reconciles their assessments, an independent sanity reviewer checks the verdict, and a human reviewer, such as a teaching assistant (TA) or instructor, approves the results before export.

Markwire accepts Git submissions, local directories, and ZIP archives. Rubrics can combine shared and individual criteria. The tool records which submission version was graded, which evidence supported the verdict, and which corrections or approvals followed.

## Try the demo

Make Python 3.11 or later available as `python` in your terminal. Install Git to run the demo or prepare Git submissions. Run these commands from the Markwire checkout. If the demo directory already exists, choose a new one.

```text
python examples/run_demo.py ../markwire-demo
python ../markwire-demo/grading.py --workspace ../markwire-demo stage-report --stage release
```

Open `../markwire-demo/reports/review.html` in a browser for the staff ledger, or `../markwire-demo/reports/release.html` for the release guide. Open `../markwire-demo/exports/student-feedback.html` to inspect the feedback export. The demo uses two fictional Git submissions and fixture assessments. It runs offline with no model account.

## Set up an assignment

Start in the Markwire checkout.

```text
python grading.py init ../grading-assignment
cd ../grading-assignment
python grading.py setup
python grading.py prepare
```

Bring the assignment brief, rubric, roster, and any starter files. Use the setup wizard or follow `SETUP.md` to configure the workspace. Confirm the course policy before preparing the submission snapshots. Configuration and grading records live in the assignment workspace.

Start each grader with the prompt for its configured alias.

```text
python grading.py prompt --role grader --grader grader_A
```

Open each agent in the assignment workspace. Give it the prompt for its assigned alias. Run `python grading.py status` to check progress and `python grading.py report` to generate the staff ledger. When the grader records are complete, run the arbiter and sanity reviewer, then record approval and export. The [setup guide](template/SETUP.md) and [grading manual](template/GRADING_MANUAL.md) cover each step.

## Review and revise grades

The offline staff ledger compares scores, feedback, and evidence for each criterion. Optional stage guides group open findings and record decisions for follow-up. The [revision and hand-off guide](docs/ongoing-grading.md) covers decisions, changes to prepared assignments, and sending a check to another machine.

Run the workflow with any coding agent that can read and write the workspace files. For model dispatch and remote worker coordination through T3 Code, see the optional [T3 runbook](docs/t3-orchestration.md).

Keep real grading workspaces outside the checkout. Each workspace contains the engine files it was prepared with. Create a sibling revision when course inputs change. See the [configuration and file reference](docs/reference.md) for the record contracts.

## Run the checks

From the Markwire checkout, run the Python suite. Set up Git first so the tests can create local submissions. To exercise the staff ledger's interactions, install Node.js and run the second command after generating the demo.

```text
python -m unittest discover -s tests -v
node tests/validate_report.js ../markwire-demo/reports/review.html
```
