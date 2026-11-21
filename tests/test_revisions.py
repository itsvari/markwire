"""Targeted revision behaviour through real, frozen workspace copies."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from typing import cast
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from grading import initialize
from fixture_json import fixture_object, fixture_objects, parse_fixture, read_fixture
from grading_core import (
    Verification,
    DraftEvaluation,
    Assignment,
    ENGINE_FILES,
    GradingError,
    Workspace,
    read_json,
    write_json,
)
from grading_coverage import coverage_template
from grading_revisions import (
    assessment_group,
    carried_template,
    create_revision,
    mapping,
    pending_assessments,
    plan_revision,
)
from grading_sources import prepare


class RevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.original = self.base / 'first grading'
        self.revised = self.base / 'revised grading'
        self.changes = self.base / 'changes.json'
        initialize(self.original)
        config_path = self.original / 'assignment.json'
        configuration = cast(Assignment, read_json(config_path))
        configuration.update({'policy_confirmed': True, 'baseline_required': False, 'graders': ['grader_A']})
        for criterion in configuration['criteria']:
            criterion['scope'] = 'submission'
        write_json(config_path, configuration)
        write_json(
            self.original / 'input/verification.json',
            {
                'version': 1,
                'checks': [
                    {
                        'id': criterion['key'],
                        'criteria': [criterion['key']],
                        'required': True,
                        'instructions': f'Inspect {criterion["key"]}.',
                        'capabilities': [],
                    }
                    for criterion in configuration['criteria']
                ],
            },
        )
        with (self.original / 'input/roster.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    'submission_id',
                    'student_id',
                    'name',
                    'github_username',
                    'repo_url',
                    'branch',
                    'commit',
                ]
            )
            git_source = self.base / 'git submission'
            subprocess.run(
                ['git', 'init', '-b', 'main', git_source],
                check=True,
                capture_output=True,
            )
            (git_source / 'answer.txt').write_text('First answer\n', encoding='utf-8', newline='\n')
            subprocess.run(
                ['git', '-C', git_source, 'add', 'answer.txt'],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    'git',
                    '-C',
                    git_source,
                    '-c',
                    'user.name=Student',
                    '-c',
                    'user.email=student@example.invalid',
                    'commit',
                    '-m',
                    'Grade this',
                ],
                check=True,
                capture_output=True,
            )
            pin = subprocess.check_output(['git', '-C', git_source, 'rev-parse', 'HEAD'], encoding='utf-8').strip()
            writer.writerow(
                [
                    'first',
                    'first',
                    'Example One',
                    'example-first',
                    str(git_source),
                    'main',
                    pin,
                ]
            )
            local_source = self.base / 'local submission'
            local_source.mkdir()
            (local_source / 'answer.txt').write_text('Second answer\n', encoding='utf-8', newline='\n')
            writer.writerow(['second', 'second', 'Example Two', '', str(local_source), '', ''])
        self.workspace = Workspace(self.original)
        self.manifest = prepare(self.workspace)
        for submission_id in self.workspace.roster:
            evidence = coverage_template(self.workspace, 'grader_A', submission_id)
            observations = [
                {
                    **observation,
                    'status': 'passed',
                    'role': 'reviewer',
                    'environment': 'pinned source',
                    'observed_at': '2026-01-01T00:00:00Z',
                    'expected': 'Answer provided.',
                    'observation': 'Answer present.',
                    'evidence': ['answer.txt'],
                }
                for observation in evidence['observations']
            ]
            write_json(
                self.original / 'coverage/grader_A' / f'{submission_id}.json',
                {**evidence, 'observations': observations},
            )
            for author in ['grader_A', 'arbiter']:
                record = self.workspace.template(submission_id, author)
                for assessment in record['scores'].values():
                    assessment.update({'score': 2, 'feedback': 'Verified the example.', 'evidence': ['answer.txt']})
                    if author == 'arbiter':
                        assessment['rationale'] = 'Verified the pinned source.'
                record['summary'] = 'Addresses the core requirement.'
                write_json(
                    self.original / 'evaluations' / author / f'{submission_id}.json',
                    record,
                )
        for submission_id, item in self.workspace.state().items():
            write_json(
                self.original / 'verification' / f'{submission_id}.json',
                {
                    'submission_id': submission_id,
                    'proposal_digest': item['proposal_digest'],
                    'checks': [
                        {
                            'claim': 'Answer present',
                            'status': 'confirmed',
                            'finding': 'Read the answer.',
                            'evidence': ['answer.txt'],
                        }
                    ],
                    'unresolved': [],
                },
            )
        for submission_id, item in self.workspace.state().items():
            write_json(
                self.original / 'review' / f'{submission_id}.json',
                {
                    'submission_id': submission_id,
                    'bundle_digest': item['bundle_digest'],
                    'reviewer': 'TA',
                    'reviewed_at': '2026-01-01T00:00:00Z',
                    'notes': 'Approved the original grade.',
                },
            )

    def revise(self, changes: dict[str, object]) -> Workspace:
        write_json(self.changes, {'reason': 'Apply the teaching team’s change.', **changes})
        command = [
            sys.executable,
            str(ROOT / 'grading.py'),
            '--workspace',
            str(self.original),
            'revise',
        ]
        preview = subprocess.run([*command, 'plan', self.changes], capture_output=True, encoding='utf-8')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        planning = parse_fixture(preview.stdout)
        plan_digest = planning['plan_digest']
        assert isinstance(plan_digest, str)
        applied = subprocess.run(
            [*command, 'create', self.changes, self.revised, '--confirm', plan_digest],
            capture_output=True,
            encoding='utf-8',
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        return Workspace(self.revised)

    def run_cli(self, workspace: Path, *arguments: str) -> str:
        result = subprocess.run(
            [
                sys.executable,
                str(workspace / 'grading.py'),
                '--workspace',
                str(workspace),
                *arguments,
            ],
            capture_output=True,
            encoding='utf-8',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_weight_change_reuses_records_and_requests_review(self) -> None:
        assignment = cast(Assignment, read_json(self.original / 'assignment.json'))
        assignment['criteria'][0]['weight'] = 90
        replacement = self.base / 'assignment.json'
        write_json(replacement, assignment)
        changed = self.revise({'assignment': replacement.name})
        for submission_id, item in changed.state().items():
            self.assertFalse(item['missing'])
            self.assertFalse(item['verification_current'])
            self.assertFalse(item['approved'])
            proposal = item['proposal']
            parent_proposal = self.workspace.state()[submission_id]['proposal']
            assert proposal is not None and parent_proposal is not None
            self.assertEqual(proposal['scores'], parent_proposal['scores'])
        self.assertTrue(all(item['approved'] for item in self.workspace.state().values()))

    def test_targeted_regrade_keeps_other_submission_approved(self) -> None:
        self.run_cli(self.original, 'export')
        original_grades = self.original / 'exports/approved-grades.csv'
        original_receipt = self.original / 'exports/receipt.json'
        original_artefacts = (
            original_grades.read_bytes(),
            original_receipt.read_bytes(),
        )
        changed = self.revise({'regrades': [{'submission_id': 'first', 'criteria': ['correctness']}]})
        current = changed.state()
        self.assertTrue(current['second']['approved'])
        self.assertTrue(current['first']['missing'])
        self.assertEqual(
            changed.prepared()['submissions']['first']['commit'],
            self.manifest['submissions']['first']['commit'],
        )
        initial = carried_template(changed, 'grader_A', 'first', changed.template('first', 'grader_A'))
        self.assertEqual(pending_assessments(initial), [{'criterion': 'correctness', 'member': None}])
        initial['summary'] = 'Reviewed the correctness appeal.'
        scores = assessment_group(initial, None)
        mapping(scores['correctness'], 'Correctness assessment').update(
            score=4, feedback='The required answer is present.', evidence=['answer.txt']
        )
        record_path = changed.root / 'evaluations/grader_A/first.json'
        write_json(record_path, initial)
        self.assertFalse(changed.state()['first']['missing'])
        verdict = cast(
            DraftEvaluation,
            json.loads(self.run_cli(changed.root, 'template', '--role', 'arbiter', '--submission', 'first')),
        )
        verdict['scores']['correctness'].update(
            {
                'score': 4,
                'feedback': 'The required answer is present.',
                'evidence': ['answer.txt'],
                'rationale': 'Confirmed the required answer in the pinned source.',
            }
        )
        verdict['summary'] = 'Revised the correctness assessment after review.'
        write_json(changed.root / 'evaluations/arbiter/first.json', verdict)
        verification = cast(
            Verification,
            json.loads(self.run_cli(changed.root, 'template', '--role', 'sanity', '--submission', 'first')),
        )
        verification['checks'] = [
            {
                'claim': 'Answer present',
                'status': 'confirmed',
                'finding': 'Rechecked the answer.',
                'evidence': ['answer.txt'],
            }
        ]
        write_json(changed.root / 'verification/first.json', verification)
        self.run_cli(
            changed.root,
            'approve',
            '--submission',
            'first',
            '--reviewer',
            'TA',
            '--notes',
            'Reviewed the amendment.',
        )
        self.run_cli(changed.root, 'export', '--amendment')
        with (changed.root / 'exports/amendment/approved-grades.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual([row['Submission ID'] for row in csv.DictReader(handle)], ['first'])
        self.assertEqual(
            set(fixture_object(read_fixture(changed.root / 'exports/amendment/receipt.json')['approvals'])),
            {'first'},
        )
        self.assertTrue(changed.state()['second']['approved'])
        self.assertEqual(
            (original_grades.read_bytes(), original_receipt.read_bytes()),
            original_artefacts,
        )
        mapping(scores['design'], 'Design assessment')['score'] = 1
        write_json(record_path, initial)
        with self.assertRaisesRegex(GradingError, 'carried assessment changed'):
            changed.state()

    def test_added_criterion_leaves_other_marks_available(self) -> None:
        assignment = cast(Assignment, read_json(self.original / 'assignment.json'))
        assignment['criteria'].append(
            {
                'key': 'tests',
                'label': 'Tests',
                'scope': 'submission',
                'max_score': 2,
                'weight': 15,
                'guidance': 'Count the specified examples.',
            }
        )
        replacement = self.base / 'assignment.json'
        write_json(replacement, assignment)
        changed = self.revise({'assignment': replacement.name})
        for submission_id in changed.roster:
            initial = carried_template(
                changed,
                'grader_A',
                submission_id,
                changed.template(submission_id, 'grader_A'),
            )
            self.assertEqual(pending_assessments(initial), [{'criterion': 'tests', 'member': None}])
            self.assertEqual(
                mapping(assessment_group(initial, None)['design'], 'Design assessment')['score'],
                2,
            )

    def test_deleted_criterion_preserves_grader_marks_and_reopens_arbiter_feedback(
        self,
    ) -> None:
        assignment = cast(Assignment, read_json(self.original / 'assignment.json'))
        assignment['criteria'] = [item for item in assignment['criteria'] if item['key'] != 'design']
        replacement = self.base / 'assignment.json'
        write_json(replacement, assignment)
        checklist = read_fixture(self.original / 'input/verification.json')
        checklist['checks'] = [item for item in fixture_objects(checklist['checks']) if item['id'] != 'design']
        checks_path = self.base / 'verification.json'
        write_json(checks_path, checklist)
        changed = self.revise(
            {
                'assignment': replacement.name,
                'inputs': {'verification.json': checks_path.name},
            }
        )
        for submission_id, item in changed.state().items():
            self.assertFalse(item['missing'])
            self.assertIsNone(item['proposal'])
            initial = carried_template(
                changed,
                'arbiter',
                submission_id,
                changed.template(submission_id, 'arbiter'),
            )
            self.assertEqual(pending_assessments(initial), [])
            self.assertEqual(initial['summary'], '')
            self.assertNotIn('design', assessment_group(initial, None))

    def test_changed_check_only_reopens_its_criterion_and_observation(self) -> None:
        checklist = read_fixture(self.original / 'input/verification.json')
        for check in fixture_objects(checklist['checks']):
            if check['id'] == 'design':
                check['instructions'] = 'Inspect design and record a second example.'
        checks_path = self.base / 'verification.json'
        write_json(checks_path, checklist)
        changed = self.revise({'inputs': {'verification.json': checks_path.name}})
        for submission_id, item in changed.state().items():
            record = carried_template(
                changed,
                'grader_A',
                submission_id,
                changed.template(submission_id, 'grader_A'),
            )
            self.assertEqual(pending_assessments(record), [{'criterion': 'design', 'member': None}])
            self.assertEqual(
                [observation['check_id'] for observation in item['coverage']['grader_A']['observations']],
                ['correctness'],
            )
            self.assertEqual(item['missing_coverage'], ['grader_A'])

    def test_second_revision_preserves_partial_assessments(self) -> None:
        changed = self.revise({'regrades': [{'submission_id': 'first', 'criteria': ['correctness']}]})
        next_changes = self.base / 'follow-up.json'
        write_json(
            next_changes,
            {
                'reason': 'Review a second request.',
                'regrades': [{'submission_id': 'second'}],
            },
        )
        plan_digest = plan_revision(changed, next_changes)['plan_digest']
        assert isinstance(plan_digest, str)
        final = self.base / 'third grading'
        create_revision(changed, final, next_changes, plan_digest, ENGINE_FILES, ROOT)
        newer = Workspace(final)
        template = carried_template(newer, 'grader_A', 'first', newer.template('first', 'grader_A'))
        self.assertEqual(
            pending_assessments(template),
            [{'criterion': 'correctness', 'member': None}],
        )

    def test_zip_resubmission_keeps_other_pin_and_approval(self) -> None:
        replacement = self.base / 'second resubmission.zip'
        with ZipFile(replacement, 'w') as archive:
            archive.writestr('answer.txt', 'Replacement answer\n')
        changed = self.revise({'resubmissions': {'second': {'source': replacement.name}}})
        new_pins = changed.prepared()['submissions']
        self.assertEqual(new_pins['first']['commit'], self.manifest['submissions']['first']['commit'])
        self.assertNotEqual(new_pins['second']['tree'], self.manifest['submissions']['second']['tree'])
        self.assertEqual(
            (changed.root / 'snapshots/second/answer.txt').read_text(encoding='utf-8'),
            'Replacement answer\n',
        )
        self.assertTrue(changed.state()['first']['approved'])
        self.assertTrue(changed.state()['second']['missing'])

    def test_stale_plan_refuses_creation(self) -> None:
        write_json(
            self.changes,
            {'reason': 'Original reason.', 'regrades': [{'submission_id': 'first'}]},
        )
        plan_digest = plan_revision(self.workspace, self.changes)['plan_digest']
        assert isinstance(plan_digest, str)
        write_json(
            self.changes,
            {'reason': 'Different reason.', 'regrades': [{'submission_id': 'second'}]},
        )
        with self.assertRaisesRegex(GradingError, 'Revision plan changed'):
            create_revision(
                self.workspace,
                self.revised,
                self.changes,
                plan_digest,
                ENGINE_FILES,
                ROOT,
            )
        self.assertFalse(self.revised.exists())
