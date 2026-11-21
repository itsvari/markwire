"""Exercise a verification floor and release gate through a prepared workspace."""

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

from fixture_json import fixture_object, fixture_objects, parse_fixture
from grading_core import Verification, DraftEvaluation, Assignment, read_json, write_json


class CoverageWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.workspace = self.directory / 'workspace'
        self.command(sys.executable, ROOT / 'grading.py', 'init', self.workspace)
        config_path = self.workspace / 'assignment.json'
        configuration = cast(Assignment, read_json(config_path))
        configuration.update({'policy_confirmed': True, 'baseline_required': False, 'graders': ['grader_A']})
        for criterion in configuration['criteria']:
            criterion['scope'] = 'submission'
            criterion['allowed_scores'] = [0, 2.5, criterion['max_score']]
        write_json(config_path, configuration)
        write_json(
            self.workspace / 'input/verification.json',
            {
                'version': 1,
                'checks': [
                    {
                        'id': 'core',
                        'instructions': 'Check the expected output.',
                        'criteria': ['correctness'],
                        'capabilities': [],
                        'required': True,
                    }
                ],
            },
        )
        self.source = self.directory / 'submission.zip'
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('answer.txt', 'Example output\n')
        with (self.workspace / 'input/roster.csv').open('w', newline='', encoding='utf-8') as handle:
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
            writer.writerow(['solo', 'student', 'Example Student', '', self.source, '', ''])

    def command(self, *arguments: Path | str, expected: int = 0) -> str:
        result = subprocess.run(
            [str(argument) for argument in arguments],
            cwd=self.directory,
            capture_output=True,
            encoding='utf-8',
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def cli(self, *arguments: str, expected: int = 0) -> str:
        return self.command(
            sys.executable,
            self.workspace / 'grading.py',
            '--workspace',
            self.workspace,
            *arguments,
            expected=expected,
        )

    def assess(self, role: str) -> None:
        alias = 'grader_A' if role == 'grader' else 'arbiter'
        record = cast(
            DraftEvaluation,
            json.loads(self.cli('template', '--role', role, '--grader', alias, '--submission', 'solo')),
        )
        for assessment in record['scores'].values():
            assessment.update({'score': 2.5, 'feedback': 'Observed the expected output.', 'evidence': ['answer.txt']})
            if role == 'arbiter':
                assessment['rationale'] = 'Reviewed the example observation.'
        record['summary'] = 'The example produced the expected output.'
        write_json(self.workspace / 'evaluations' / alias / 'solo.json', record)

    def test_zip_half_marks_coverage_and_refuted_grade_claim(self) -> None:
        self.cli('prepare')
        self.assess('grader')
        self.assertEqual(fixture_object(parse_fixture(self.cli('status'))['solo'])['missing_coverage'], ['grader_A'])
        self.assertIn(
            'required verification record',
            self.cli('prompt', '--role', 'arbiter', expected=1),
        )

        coverage = parse_fixture(self.cli('coverage-template', '--grader', 'grader_A', '--submission', 'solo'))
        fixture_objects(coverage['observations'])[0].update(
            status='passed',
            role='reviewer',
            environment='pinned ZIP',
            observed_at='2026-01-01T00:00:00Z',
            expected='Example output',
            observation='Saw the expected text.',
            evidence=['answer.txt'],
        )
        write_json(self.workspace / 'coverage/grader_A/solo.json', coverage)
        self.assertEqual(
            json.loads(self.cli('coverage-template', '--grader', 'grader_A', '--submission', 'solo')),
            coverage,
        )
        self.cli('prompt', '--role', 'arbiter')
        self.assess('arbiter')
        verification = cast(Verification, json.loads(self.cli('template', '--role', 'sanity', '--submission', 'solo')))
        verification['checks'][0].update(
            {
                'claim': 'Expected result appeared',
                'status': 'refuted',
                'finding': 'The actual fixture expected different text.',
                'evidence': ['answer.txt'],
            }
        )
        write_json(self.workspace / 'verification/solo.json', verification)
        self.assertFalse(fixture_object(parse_fixture(self.cli('status'))['solo'])['ready'])
        self.assertIn(
            'resolved sanity review',
            self.cli(
                'approve',
                '--submission',
                'solo',
                '--reviewer',
                'TA',
                '--notes',
                'Review',
                expected=1,
            ),
        )

    def test_local_archive_cannot_write_outside_the_snapshot(self) -> None:
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('../outside.txt', 'Unexpected content')
        self.assertIn('Unsupported submission ZIP member', self.cli('prepare', expected=1))
        self.assertFalse((self.workspace / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
