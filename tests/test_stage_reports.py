"""Stage findings and report-bound decisions through the portable CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from typing import cast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture_json import fixture_object, fixture_objects, fixture_text, parse_fixture, read_fixture
from grading_core import Evaluation, DraftEvaluation, Assignment, read_json, write_json


class StageReportTest(unittest.TestCase):
    def run_command(self, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(part) for part in arguments], encoding='utf-8', capture_output=True)

    def checked_command(self, *arguments: str | Path) -> str:
        result = self.run_command(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cli(self, *arguments: str | Path) -> str:
        return self.checked_command(
            sys.executable,
            self.workspace / 'grading.py',
            '--workspace',
            self.workspace,
            *arguments,
        )

    def rejected(self, *arguments: str | Path) -> str:
        result = self.run_command(
            sys.executable,
            self.workspace / 'grading.py',
            '--workspace',
            self.workspace,
            *arguments,
        )
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.workspace = self.parent / 'workspace'
        self.checked_command(sys.executable, ROOT / 'grading.py', 'init', self.workspace)
        repository = self.parent / 'submission'
        self.checked_command('git', 'init', '-b', 'main', repository)
        (repository / 'answer.txt').write_text('Example answer\n', encoding='utf-8', newline='\n')
        self.checked_command('git', '-C', repository, 'add', 'answer.txt')
        self.checked_command(
            'git',
            '-C',
            repository,
            '-c',
            'user.email=fixture@example.invalid',
            '-c',
            'user.name=Fixture',
            'commit',
            '-m',
            'Add example answer',
        )
        assignment = cast(Assignment, read_json(self.workspace / 'assignment.json'))
        assignment.update({'policy_confirmed': True, 'baseline_required': False, 'graders': ['grader_A', 'grader_B']})
        assignment['snapshot_policy']['mode'] = 'head'
        assignment['review_thresholds']['criterion_spread'] = 2
        for criterion in assignment['criteria']:
            criterion['scope'] = 'submission'
        write_json(self.workspace / 'assignment.json', assignment)
        (self.workspace / 'input' / 'roster.csv').write_text(
            'submission_id,student_id,name,github_username,repo_url,branch,commit\n'
            f'example,member,Example Student,example-member,{repository},main,\n',
            encoding='utf-8',
        )
        write_json(
            self.workspace / 'input' / 'verification.json',
            {
                'version': 1,
                'checks': [
                    {
                        'id': 'create-profile',
                        'instructions': 'Create a profile',
                        'criteria': ['correctness'],
                        'capabilities': [],
                        'required': True,
                    }
                ],
            },
        )
        self.cli('prepare')
        for author, mark, status in [
            ('grader_A', 3, 'passed'),
            ('grader_B', 2, 'failed'),
        ]:
            record = cast(
                DraftEvaluation,
                json.loads(
                    self.cli(
                        'template',
                        '--role',
                        'grader',
                        '--submission',
                        'example',
                        '--grader',
                        author,
                    )
                ),
            )
            for criterion_key, assessment in record['scores'].items():
                assessment.update(
                    {
                        'score': mark if criterion_key == 'correctness' else 2,
                        'feedback': 'Typed fixture.',
                        'evidence': ['answer.txt:1'],
                    }
                )
            record.update(
                {
                    'summary': 'Fixture summary.',
                    'flags': [
                        'Profile check needs a retry.',
                        f'Profile test needs {author} follow-up <script>alert(1)</script>.',
                    ],
                }
            )
            write_json(self.workspace / 'evaluations' / author / 'example.json', record)
            coverage = parse_fixture(self.cli('coverage-template', '--submission', 'example', '--grader', author))
            fixture_objects(coverage['observations'])[0].update(
                status=status,
                role='owner',
                environment='hosted',
                observed_at='2026-01-01T10:00:00Z',
                expected='Profile is saved',
                observation=f'Grader observed {status}.',
                evidence=['answer.txt:1'],
            )
            write_json(self.workspace / 'coverage' / author / 'example.json', coverage)

    def test_grouped_evidence_and_weighted_disagreement_reach_both_reports(
        self,
    ) -> None:
        draft = parse_fixture(self.cli('stage-report', '--stage', 'grading', '--draft'))
        shared = next(
            finding
            for finding in fixture_objects(draft['findings'])
            if finding['title'] == 'Profile check needs a retry.'
        )
        self.assertEqual({source['author'] for source in fixture_objects(shared['sources'])}, {'grader_A', 'grader_B'})
        disagreement = next(finding for finding in fixture_objects(draft['findings']) if finding['topic'] == 'Scoring')
        self.assertEqual(disagreement['impact_percentage_points'], 17.5)
        self.assertEqual(disagreement['severity'], 'grade_impact')
        coverage = next(
            finding for finding in fixture_objects(draft['findings']) if finding['topic'] == 'Verification coverage'
        )
        self.assertEqual(coverage['title'], 'Mixed observations · create-profile')
        self.assertEqual(len(fixture_objects(coverage['sources'])), 2)
        self.assertEqual(fixture_object(draft['progress'])['complete'], 2)
        self.assertFalse(
            any(finding['title'] == 'Missing arbitration' for finding in fixture_objects(draft['findings']))
        )

        self.cli('stage-report', '--stage', 'grading')
        html = (self.workspace / 'reports' / 'grading.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('Decision hand-off', html)
        self.cli('report')
        columns = (self.workspace / 'reports' / 'review.html').read_text(encoding='utf-8')
        self.assertIn('"impact": 17.5', columns)
        self.assertIn('grading.html', columns)

    def test_curated_options_bind_to_snapshot_and_import_once(self) -> None:
        draft = parse_fixture(self.cli('stage-report', '--stage', 'grading', '--draft'))
        sources = [
            finding['id']
            for finding in fixture_objects(draft['findings'])
            if 'follow-up' in fixture_text(finding['title'])
        ]
        curation = self.parent / 'curation.json'
        write_json(
            curation,
            {
                'source_digest': draft['source_digest'],
                'groups': [
                    {
                        'finding_ids': sources,
                        'title': 'Retest hosted profiles',
                        'topic': 'Hosted profiles',
                        'summary': 'Both reports request a targeted profile check.',
                        'next_step': 'Repeat the check using a clean fixture.',
                        'question': 'Request the retry?',
                        'options': [
                            {
                                'id': 'yes',
                                'label': 'Yes, rerun',
                                'consequence': 'The grader runs a focused test.',
                                'owner': 'grader',
                            },
                            {
                                'id': 'no',
                                'label': 'No, defer',
                                'consequence': 'Leave the finding open.',
                                'owner': 'ta',
                            },
                        ],
                    }
                ],
            },
        )
        self.cli('stage-report', '--stage', 'grading', '--curation', curation)
        report = read_fixture(self.workspace / 'reports' / 'grading.json')
        merged = next(
            finding for finding in fixture_objects(report['findings']) if finding['title'] == 'Retest hosted profiles'
        )
        self.assertEqual(len(fixture_objects(merged['sources'])), 2)
        self.assertFalse(
            any('follow-up' in fixture_text(finding['title']) for finding in fixture_objects(report['findings']))
        )
        response = self.parent / 'response.json'
        base = {
            'schemaVersion': 1,
            'runId': report['run_id'],
            'stage': 'grading',
            'reviewDigest': report['review_digest'],
        }
        write_json(
            response,
            {**base, 'decisions': [{'issueId': merged['id'], 'optionId': 'unknown'}]},
        )
        self.assertIn(
            'Unknown option',
            self.rejected('import-decisions', response, '--reviewer', 'Example TA'),
        )

        actionable = [
            finding
            for finding in fixture_objects(report['findings'])
            if finding['status'] == 'open' and finding['options']
        ]
        self.assertGreater(len(actionable), 1)
        write_json(
            response,
            {**base, 'decisions': [{'issueId': merged['id'], 'optionId': 'yes'}]},
        )
        self.assertIn(
            'remediation question',
            self.rejected('import-decisions', response, '--reviewer', 'Example TA'),
        )
        self.assertEqual(list((self.workspace / 'decisions').glob('*.json')), [])

        decisions = [
            {'issueId': finding['id'], 'optionId': 'yes' if finding['id'] == merged['id'] else 'defer'}
            for finding in actionable
        ]
        write_json(response, {**base, 'decisions': decisions})
        receipt_path = Path(self.cli('import-decisions', response, '--reviewer', 'Example TA'))
        actions = fixture_objects(read_fixture(receipt_path)['actions'])
        self.assertEqual(len(actions), len(actionable))
        self.assertEqual(
            next(action for action in actions if action['issue_id'] == merged['id'])['owner'],
            'grader',
        )
        self.assertTrue(all(action['owner'] == 'ta' for action in actions if action['issue_id'] != merged['id']))
        write_json(response, {**base, 'decisions': list(reversed(decisions))})
        self.assertEqual(
            receipt_path,
            Path(self.cli('import-decisions', response, '--reviewer', 'Example TA')),
        )
        self.assertEqual(len(list(receipt_path.parent.glob('*.json'))), 1)
        self.assertEqual(list((self.workspace / 'review').glob('*.json')), [])

        changed = self.workspace / 'evaluations' / 'grader_A' / 'example.json'
        evaluation = cast(Evaluation, read_json(changed))
        evaluation['summary'] = 'Updated overall remark.'
        write_json(changed, evaluation)
        write_json(
            response,
            {
                **base,
                'decisions': [
                    {**decision, 'optionId': 'no'} if decision['issueId'] == merged['id'] else decision
                    for decision in decisions
                ],
            },
        )
        self.assertIn(
            'stale',
            self.rejected('import-decisions', response, '--reviewer', 'Example TA'),
        )

    def test_blocked_remote_check_offers_follow_up_with_returned_evidence(self) -> None:
        specification = self.parent / 'remote-task.json'
        write_json(
            specification,
            {
                'task_id': 'remote-profile',
                'attempt': 1,
                'submission_id': 'example',
                'capabilities': ['browser'],
                'checks': [{'id': 'sign-in', 'instructions': 'Open the sign-in page'}],
                'files': ['input/policy.md'],
                'include_snapshot': False,
            },
        )
        archive = self.parent / 'task.zip'
        worker = self.parent / 'worker'
        self.cli('bundle', 'pack', specification, archive)
        self.cli('bundle', 'open', archive, worker)
        report = read_fixture(worker / 'result.json')
        report['environment'] = 'Fixture browser environment'
        fixture_objects(report['observations'])[0].update(status='blocked', observation='Sign-in needs the TA.')
        write_json(worker / 'result.json', report)
        result = self.parent / 'result.zip'
        self.cli('bundle', 'finish', worker, result)
        self.cli('bundle', 'import', result)

        draft = parse_fixture(self.cli('stage-report', '--stage', 'grading', '--draft'))
        self.assertTrue(
            any(
                '1 of 1 task attempts imported' in fixture_text(step['summary'])
                for step in fixture_objects(draft['stops'])
            )
        )
        finding = next(item for item in fixture_objects(draft['findings']) if item['topic'] == 'Remote checks')
        self.assertEqual(finding['title'], 'Blocked remote check · sign-in')
        self.assertEqual(fixture_objects(finding['options'])[0]['label'], 'Request another remote attempt')
        reference = fixture_text(fixture_objects(finding['sources'])[0]['reference']).split('#')[0]
        self.assertTrue((self.workspace / reference).is_file())

    def test_reconciled_score_comparison_does_not_require_an_answer(self) -> None:
        verdict = cast(
            DraftEvaluation,
            json.loads(self.cli('template', '--role', 'arbiter', '--submission', 'example')),
        )
        for assessment in verdict['scores'].values():
            assessment.update(
                {
                    'score': 3,
                    'feedback': 'The example demonstrates the expected result.',
                    'evidence': ['answer.txt:1'],
                    'rationale': 'Reviewed the pinned answer.',
                }
            )
        verdict['summary'] = 'The example meets the core requirement.'
        write_json(self.workspace / 'evaluations/arbiter/example.json', verdict)
        self.cli('stage-report', '--stage', 'arbitration')
        report = read_fixture(self.workspace / 'reports/arbitration.json')
        findings = fixture_objects(report['findings'])
        reconciled = [finding for finding in findings if finding['status'] == 'reconciled']
        self.assertTrue(reconciled)
        self.assertTrue(all(not finding['options'] for finding in reconciled))
        decisions = [
            {'issueId': finding['id'], 'optionId': 'defer'}
            for finding in findings
            if finding['status'] == 'open' and finding['options']
        ]
        self.assertTrue(decisions)
        response = self.parent / 'response.json'
        write_json(
            response,
            {
                'schemaVersion': 1,
                'runId': report['run_id'],
                'stage': 'arbitration',
                'reviewDigest': report['review_digest'],
                'decisions': decisions,
            },
        )
        receipt = Path(self.cli('import-decisions', response, '--reviewer', 'Example TA'))
        self.assertEqual(len(fixture_objects(read_fixture(receipt)['actions'])), len(decisions))


if __name__ == '__main__':
    unittest.main()
