"""Real local Git histories and public CLI, with no student data or network."""

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture_json import fixture_object, fixture_objects, object_at, read_fixture
from grading_core import (
    Verification,
    Correction,
    Assignment,
    DraftEvaluation,
    Evaluation,
    SourceManifest,
    Workspace,
    read_json,
    write_json,
)
from grading_reports import csv_text


class RenderedScore(TypedDict):
    raw: int | float
    weighted: float


class RenderedAssessment(TypedDict):
    feedback: str


class RenderedCriterion(TypedDict):
    member: str | None
    proposal: RenderedAssessment | None
    spread: int | float | None
    different: bool
    candidates: dict[str, RenderedAssessment | None]


class RenderedMember(TypedDict):
    proposed: RenderedScore | None
    totals: dict[str, RenderedScore | None]


class RenderedSubmission(TypedDict):
    id: str
    members: list[RenderedMember]
    rows: list[RenderedCriterion]
    approved: bool
    ready: bool
    flags: list[str]
    spread: int | float | None


class ReportPayload(TypedDict):
    submissions: list[RenderedSubmission]
    graders: list[object]
    maximum: int | float


class ReleaseReceipt(TypedDict):
    grades_sha256: str
    feedback_files_sha256: dict[str, str]


def command(
    *arguments: str | Path,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        env=environment,
        capture_output=True,
        encoding='utf-8',
    )


CUTOFF = '2030-04-15T12:00:00+00:00'
SOURCE_TIMESTAMPS = ('2030-04-15T11:59:59+00:00', CUTOFF, '2030-04-16T12:00:00+00:00')
REVIEWERS = ('grader_A', 'grader_B', 'arbiter')
SHARED_MARK = 3
LOWER_MARK = 2


class ReportReader(HTMLParser):
    def __init__(self, page: str) -> None:
        super().__init__()
        self.scripts = 0
        self.data = ''
        self.in_data = False
        self.resources: list[dict[str, str | None]] = []
        self.feed(page)
        self.payload = cast(ReportPayload, json.loads(self.data))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == 'script':
            self.scripts += 1
            self.in_data = attributes.get('id') == 'report-data'
        if tag in ('script', 'link') and ('src' in attributes or 'href' in attributes):
            self.resources.append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == 'script':
            self.in_data = False

    def handle_data(self, data: str) -> None:
        if self.in_data:
            self.data += data


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.base = Path(directory)
        self.root = self.base / 'grading workspace'
        self.run_cli('init', self.root, workspace=False)
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration.update(
            {
                'policy_confirmed': True,
                'baseline_required': False,
                'graders': ['grader_A', 'grader_B'],
                'snapshot_policy': {
                    'mode': 'deadline',
                    'deadline': CUTOFF,
                    'boundary': 'exclusive',
                    'timestamp': 'committer',
                },
            }
        )
        for criterion in configuration['criteria']:
            criterion['scope'] = 'submission'
        write_json(self.root / 'assignment.json', configuration)
        self.commits: dict[str, list[str]] = {}
        with (self.root / 'input/roster.csv').open('w', encoding='utf-8', newline='') as handle:
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
            for submission_id, name in [
                ('s01', 'Example One'),
                ('s02', '=Example Two'),
            ]:
                repository = self.base / submission_id
                self.checked_command('git', 'init', '-b', 'main', repository)
                self.checked_command('git', '-C', repository, 'config', 'user.name', 'Fixture')
                self.checked_command(
                    'git',
                    '-C',
                    repository,
                    'config',
                    'user.email',
                    'fixture@example.invalid',
                )
                commits = []
                for iteration, timestamp in enumerate(SOURCE_TIMESTAMPS):
                    (repository / 'app.txt').write_text(f'Implementation {iteration}\n', encoding='utf-8', newline='\n')
                    self.checked_command('git', '-C', repository, 'add', 'app.txt')
                    environment = {
                        **os.environ,
                        'GIT_AUTHOR_DATE': timestamp,
                        'GIT_COMMITTER_DATE': timestamp,
                    }
                    self.checked_command(
                        'git',
                        '-C',
                        repository,
                        'commit',
                        '-m',
                        f'Iteration {iteration}',
                        environment=environment,
                    )
                    commits.append(self.checked_command('git', '-C', repository, 'rev-parse', 'HEAD').strip())
                self.commits[submission_id] = commits
                writer.writerow(
                    [
                        submission_id,
                        submission_id,
                        name,
                        f'example-{submission_id}',
                        repository,
                        'main',
                        '',
                    ]
                )

    def checked_command(self, *arguments: str | Path, environment: Mapping[str, str] | None = None) -> str:
        result = command(*arguments, environment=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def run_cli(self, *arguments: str | Path, workspace: bool = True, fail: bool = False) -> str:
        script = self.root / 'grading.py' if workspace else ROOT / 'grading.py'
        command_arguments: list[str | Path] = [sys.executable, script]
        if workspace:
            command_arguments += ['--workspace', self.root]
        result = command(*command_arguments, *arguments)
        if fail:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout + (result.stderr if fail else '')

    def populate(self) -> None:
        self.run_cli('prepare')
        for submission_id in self.commits:
            for author in REVIEWERS:
                record = cast(
                    DraftEvaluation,
                    json.loads(
                        self.run_cli(
                            'template',
                            '--role',
                            'arbiter' if author == 'arbiter' else 'grader',
                            '--grader',
                            author,
                            '--student',
                            submission_id,
                        )
                    ),
                )
                for assessments in [
                    record['scores'],
                    *record['individual_scores'].values(),
                ]:
                    for criterion, assessment in assessments.items():
                        assessment.update(
                            {
                                'score': LOWER_MARK if author == 'grader_B' else SHARED_MARK,
                                'feedback': f'{criterion}: documented behaviour <script>bad()</script>',
                                'evidence': ['app.txt:1'],
                            }
                        )
                        if author == 'arbiter':
                            assessment['rationale'] = 'Inspected the pinned implementation.'
                record['summary'] = 'Fictional evaluation for workflow verification.'
                write_json(self.root / 'evaluations' / author / f'{submission_id}.json', record)
            self.verify(submission_id)

    def verify(self, submission_id: str) -> None:
        verification = cast(
            Verification, json.loads(self.run_cli('template', '--role', 'sanity', '--student', submission_id))
        )
        verification['checks'] = [
            {
                'claim': 'Selected source is eligible',
                'status': 'confirmed',
                'finding': 'Inspected manifest and source history.',
                'evidence': ['manifest.json', 'app.txt:1'],
            }
        ]
        write_json(self.root / 'verification' / f'{submission_id}.json', verification)

    def roster_rows(self) -> list[dict[str, str]]:
        with (self.root / 'input/roster.csv').open(encoding='utf-8', newline='') as handle:
            return cast(list[dict[str, str]], list(csv.DictReader(handle)))

    def write_roster(self, rows: Sequence[dict[str, str]]) -> None:
        with (self.root / 'input/roster.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def make_group(self, individual_criteria: Sequence[str] = ('design',)) -> None:
        rows = self.roster_rows()
        rows.insert(
            1,
            {
                **rows[0],
                'student_id': 's03',
                'name': 'Example Three',
                'github_username': 'example-s03',
            },
        )
        self.write_roster(rows)
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        for criterion in configuration['criteria']:
            criterion['scope'] = 'individual' if criterion['key'] in individual_criteria else 'submission'
        write_json(self.root / 'assignment.json', configuration)

    def approve_all(self) -> None:
        for submission_id in self.commits:
            self.run_cli(
                'approve',
                '--submission',
                submission_id,
                '--reviewer',
                'Fixture TA',
                '--notes',
                'Reviewed all members and remarks.',
            )

    def test_mixed_group_totals_export_and_correction(self) -> None:
        self.make_group()
        self.populate()
        record_path = self.root / 'evaluations/arbiter/s01.json'
        verdict = cast(Evaluation, read_json(record_path))
        verdict['individual_scores']['s01']['design']['score'] = 4
        verdict['individual_scores']['s01']['design']['feedback'] = 'A private finding for Example One.'
        verdict['individual_scores']['s03']['design']['score'] = 1
        verdict['individual_scores']['s03']['design']['feedback'] = 'A private finding for Example Three.'
        write_json(record_path, verdict)
        self.verify('s01')
        workspace = Workspace(self.root)
        self.assertEqual(workspace.totals(verdict, 's01'), (7, 82.5))
        self.assertEqual(workspace.totals(verdict, 's03'), (4, 60.0))
        self.assertEqual(len(workspace.state()['s01']['submission']['members']), 2)
        self.assertEqual(len(workspace.prepared()['submissions']['s01']['members']), 2)
        self.run_cli('report')
        page = (self.root / 'reports/review.html').read_text(encoding='utf-8')
        self.assertIn('Example Three', page)
        self.assertIn('example-s03', page)
        group = ReportReader(page).payload['submissions'][0]
        self.assertEqual(
            [member['proposed'] for member in group['members']],
            [{'raw': 7, 'weighted': 82.5}, {'raw': 4, 'weighted': 60.0}],
        )
        self.assertEqual([criterion['member'] for criterion in group['rows']], [None, 's01', 's03'])
        proposal = group['rows'][2]['proposal']
        assert proposal is not None
        self.assertEqual(proposal['feedback'], 'A private finding for Example Three.')
        self.approve_all()
        self.run_cli('export')
        with (self.root / 'exports/approved-grades.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertIn('82.5', json.dumps(rows))
        self.assertIn('60.0', json.dumps(rows))
        feedback = read_json(self.root / 'exports/student-feedback.json')
        assert isinstance(feedback, list)
        self.assertEqual(len(feedback), 3)
        self.assertNotIn('rationale', json.dumps(feedback))
        for student_id, own, other in [
            ('s01', 'One', 'Three'),
            ('s03', 'Three', 'One'),
        ]:
            personal = (self.root / 'exports/feedback/s01' / f'{student_id}.html').read_text(encoding='utf-8')
            self.assertIn(f'A private finding for Example {own}.', personal)
            self.assertNotIn(f'A private finding for Example {other}.', personal)
            self.assertNotIn('grader_A', personal)
        receipt = cast(ReleaseReceipt, read_json(self.root / 'exports/receipt.json'))
        self.assertEqual(
            set(receipt['feedback_files_sha256']),
            {'feedback/s01/s01.html', 'feedback/s01/s03.html', 'feedback/s02/s02.html'},
        )
        for relative_path, expected in receipt['feedback_files_sha256'].items():
            self.assertEqual(
                expected,
                hashlib.sha256((self.root / 'exports' / relative_path).read_bytes()).hexdigest(),
            )
        correction = cast(
            Correction, json.loads(self.run_cli('template', '--role', 'correction', '--submission', 's01'))
        )
        correction.update({'reason': 'Rechecked individual design evidence.', 'evidence': ['app.txt:1']})
        correction['replacement']['individual_scores']['s03']['design']['score'] = 2
        write_json(self.root / 'corrections/s01/001.json', correction)
        self.assertFalse(Workspace(self.root).state()['s01']['approved'])
        self.run_cli('export', fail=True)
        self.verify('s01')
        self.run_cli('export', fail=True)
        self.run_cli(
            'approve',
            '--submission',
            's01',
            '--reviewer',
            'Fixture TA',
            '--notes',
            'Reviewed corrected individual score.',
        )
        self.run_cli('export')

    def test_group_record_requires_exact_subjects_and_overall_remark(self) -> None:
        self.make_group()
        self.populate()
        record_path = self.root / 'evaluations/grader_A/s01.json'
        valid = cast(dict[str, object], read_json(record_path))
        invalid = []
        record = copy.deepcopy(valid)
        del fixture_object(record['individual_scores'])['s03']
        invalid.append(record)
        record = copy.deepcopy(valid)
        members = fixture_object(record['individual_scores'])
        members['s02'] = members.pop('s03')
        invalid.append(record)
        record = copy.deepcopy(valid)
        fixture_object(record['individual_scores'])['s03'] = {}
        invalid.append(record)
        record = copy.deepcopy(valid)
        member = fixture_object(fixture_object(record['individual_scores'])['s01'])
        fixture_object(record['scores'])['design'] = member['design']
        invalid.append(record)
        record = copy.deepcopy(valid)
        member = fixture_object(fixture_object(record['individual_scores'])['s03'])
        member['correctness'] = fixture_object(record['scores'])['correctness']
        invalid.append(record)
        record = copy.deepcopy(valid)
        del record['summary']
        invalid.append(record)
        record = copy.deepcopy(valid)
        record['summary'] = '   '
        invalid.append(record)
        for index, record in enumerate(invalid):
            with self.subTest(index=index):
                write_json(record_path, record)
                self.run_cli('validate', fail=True)
        write_json(record_path, valid)
        self.run_cli('validate')
        schema = read_fixture(self.root / 'schemas/evaluation.json')
        member_rule = next(
            rule
            for rule in fixture_objects(schema['allOf'])
            if object_at(rule, 'if', 'properties', 'submission_id')['const'] == 's01'
        )
        member_schema = object_at(member_rule, 'then', 'properties', 'individual_scores')
        self.assertEqual(member_schema['required'], ['s01', 's03'])
        self.assertEqual(object_at(member_schema, 'properties', 's03')['required'], ['design'])

    def test_all_individual_group(self) -> None:
        self.make_group(individual_criteria=('correctness', 'design'))
        self.populate()
        record = cast(Evaluation, read_json(self.root / 'evaluations/arbiter/s01.json'))
        self.assertEqual(record['scores'], {})
        self.assertEqual(Workspace(self.root).totals(record, 's03'), (6, 75.0))
        self.run_cli('report')
        self.approve_all()
        self.run_cli('export')

    def test_all_shared_group_uses_no_individual_scores(self) -> None:
        self.make_group(individual_criteria=())
        self.populate()
        record = cast(Evaluation, read_json(self.root / 'evaluations/arbiter/s01.json'))
        self.assertEqual(record['individual_scores'], {})
        workspace = Workspace(self.root)
        self.assertEqual(workspace.totals(record, 's01'), workspace.totals(record, 's03'))
        self.run_cli('report')
        self.approve_all()
        self.run_cli('export')

    def test_unresolved_scopes_block_preparation(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration['criteria'][0]['scope'] = 'unresolved'
        write_json(self.root / 'assignment.json', configuration)
        self.assertEqual(Workspace(self.root).criteria['correctness']['scope'], 'unresolved')
        self.assertIn('Confirm submission or individual scope', self.run_cli('prepare', fail=True))
        self.assertFalse((self.root / 'manifest.json').exists())
        configuration['criteria'][0]['scope'] = 'guess-from-git'
        write_json(self.root / 'assignment.json', configuration)
        self.assertIn('Criterion scope', self.run_cli('prepare', fail=True))

    def test_group_roster_source_and_identity_checks(self) -> None:
        self.make_group()
        valid = self.roster_rows()
        for key, value in [
            ('repo_url', str(self.base / 's02')),
            ('branch', 'another'),
            ('commit', self.commits['s01'][0]),
        ]:
            with self.subTest(key=key):
                rows = copy.deepcopy(valid)
                rows[1][key] = value
                self.write_roster(rows)
                self.assertIn('members must share', self.run_cli('prepare', fail=True))
        rows = copy.deepcopy(valid)
        rows[1]['student_id'] = rows[0]['student_id']
        self.write_roster(rows)
        self.assertIn('Duplicate student', self.run_cli('prepare', fail=True))
        rows = copy.deepcopy(valid)
        rows[1]['github_username'] = rows[0]['github_username'].upper()
        self.write_roster(rows)
        self.assertIn('Duplicate GitHub account', self.run_cli('prepare', fail=True))
        self.write_roster(valid)
        self.run_cli('prepare')
        rows = copy.deepcopy(valid)
        rows[1]['name'] = 'Changed attribution'
        self.write_roster(rows)
        self.assertIn('Inputs changed', self.run_cli('validate', fail=True))
        self.write_roster(valid)
        manifest = cast(SourceManifest, read_json(self.root / 'manifest.json'))
        manifest['submissions']['s01']['members'].pop()
        write_json(self.root / 'manifest.json', manifest)
        self.assertIn('manifest membership mismatch', self.run_cli('validate', fail=True))

    def test_candidate_overall_remark_invalidates_approval(self) -> None:
        self.populate()
        self.approve_all()
        self.assertTrue(Workspace(self.root).state()['s01']['approved'])
        record_path = self.root / 'evaluations/grader_B/s01.json'
        candidate = cast(Evaluation, read_json(record_path))
        candidate['summary'] = 'The function is clear. In future work, document accepted inputs before implementing.'
        write_json(record_path, candidate)
        state = Workspace(self.root).state()['s01']
        self.assertFalse(state['verification_current'])
        self.assertFalse(state['approved'])
        self.run_cli('export', fail=True)

    def test_import_requires_explicit_identity_columns(self) -> None:
        rows = self.roster_rows()
        source = self.base / 'source.csv'
        with source.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Team', 'Student', 'Name', 'GitHub', 'Repo URL', 'Branch'])
            for row in rows:
                writer.writerow(
                    [
                        row['submission_id'],
                        row['student_id'],
                        row['name'],
                        row['github_username'],
                        row['repo_url'],
                        row['branch'],
                    ]
                )
        self.assertIn(
            '--student-column',
            self.run_cli('import-roster', '--csv', source, fail=True),
        )
        self.run_cli(
            'import-roster',
            '--csv',
            source,
            '--submission-column',
            'Team',
            '--student-column',
            'Student',
            '--github-column',
            'GitHub',
        )
        self.assertEqual(self.roster_rows(), rows)

    def test_full_workflow_and_stale_approval(self) -> None:
        self.populate()
        manifest = cast(SourceManifest, read_json(self.root / 'manifest.json'))
        for submission_id in self.commits:
            self.assertEqual(
                manifest['submissions'][submission_id]['commit'],
                self.commits[submission_id][0],
            )
        before = (self.root / 'manifest.json').read_bytes()
        self.run_cli('prepare')
        self.assertEqual(before, (self.root / 'manifest.json').read_bytes())
        self.run_cli('validate')
        self.run_cli('prompt', '--role', 'arbiter')
        self.run_cli('prompt', '--role', 'sanity')
        self.run_cli('report')
        page = (self.root / 'reports/review.html').read_text(encoding='utf-8')
        reader = ReportReader(page)
        self.assertEqual(
            [submission['id'] for submission in reader.payload['submissions']],
            ['s01', 's02'],
        )
        self.assertEqual(reader.scripts, 2)
        self.assertEqual(reader.resources, [], 'generated report is standalone')
        submission = reader.payload['submissions'][0]
        proposal = submission['rows'][0]['proposal']
        assert proposal is not None
        self.assertEqual(
            proposal['feedback'],
            'correctness: documented behaviour <script>bad()</script>',
        )
        self.assertNotIn('<script>bad()</script>', page)
        self.assertEqual(submission['members'][0]['proposed'], {'raw': 6, 'weighted': 75.0})
        self.assertEqual(reader.payload['maximum'], 8)
        self.assertFalse(submission['approved'])
        self.assertTrue(submission['ready'])
        self.assertIn('Internal TA review', page)
        self.assertNotIn('Fictional data · TA preview', page)
        with (self.root / 'reports/comparison.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1][2:7], ['3', '2', '2.5', '0.5', '1'])
        self.run_cli('export', fail=True)
        for submission_id in self.commits:
            self.run_cli(
                'approve',
                '--student',
                submission_id,
                '--reviewer',
                'Fixture TA',
                '--notes',
                'Manually checked the fictional record.',
            )
        self.run_cli('export')
        exported = (self.root / 'exports/approved-grades.csv').read_bytes()
        receipt = cast(ReleaseReceipt, read_json(self.root / 'exports/receipt.json'))
        self.assertEqual(receipt['grades_sha256'], hashlib.sha256(exported).hexdigest())
        self.assertIn("'=Example Two", exported.decode())
        feedback = read_json(self.root / 'exports/student-feedback.json')
        assert isinstance(feedback, list)
        self.assertEqual(len(feedback), 2)
        self.assertNotIn('rationale', json.dumps(feedback))
        self.assertNotIn(
            'grader_A',
            (self.root / 'exports/student-feedback.html').read_text(encoding='utf-8'),
        )
        original = (self.root / 'evaluations/arbiter/s01.json').read_bytes()
        correction = cast(Correction, json.loads(self.run_cli('template', '--role', 'correction', '--student', 's01')))
        correction.update({'reason': 'Accepted source recheck.', 'evidence': ['app.txt:1']})
        correction['replacement']['scores']['correctness']['score'] = 4
        write_json(self.root / 'corrections/s01/001.json', correction)
        self.run_cli('report')
        self.assertIn(
            'Stale sanity review',
            (self.root / 'reports/review.html').read_text(encoding='utf-8'),
        )
        self.run_cli('export', fail=True)
        self.verify('s01')
        self.run_cli('export', fail=True)
        self.run_cli(
            'approve',
            '--student',
            's01',
            '--reviewer',
            'Fixture TA',
            '--notes',
            'Verified correction.',
        )
        self.run_cli('export')
        self.assertEqual(original, (self.root / 'evaluations/arbiter/s01.json').read_bytes())
        self.assertIn(
            '92.5',
            (self.root / 'exports/approved-grades.csv').read_text(encoding='utf-8'),
        )
        correction['parent_digest'] = 'wrong'
        write_json(self.root / 'corrections/s01/002.json', correction)
        self.assertIn('stale correction parent', self.run_cli('validate', fail=True))

    def test_columns_partial_dynamic_graders_and_feedback_differences(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration['graders'] = ['grader_A', 'grader_B', 'grader_C', 'grader_D']
        write_json(self.root / 'assignment.json', configuration)
        self.run_cli('prepare')
        for author in ['grader_A', 'grader_B']:
            record = cast(
                DraftEvaluation,
                json.loads(
                    self.run_cli(
                        'template',
                        '--role',
                        'grader',
                        '--grader',
                        author,
                        '--submission',
                        's01',
                    )
                ),
            )
            for value in record['scores'].values():
                value.update(
                    {
                        'score': 2,
                        'feedback': f'{author}: a different interpretation.',
                        'evidence': [f'{author}.txt:1'],
                    }
                )
            record['summary'] = f'{author} overall remark </script><script>bad()</script>'
            write_json(self.root / 'evaluations' / author / 's01.json', record)
        self.run_cli('report')
        reader = ReportReader((self.root / 'reports/review.html').read_text(encoding='utf-8'))
        self.assertEqual(reader.scripts, 2)
        self.assertEqual(len(reader.payload['graders']), 4)
        submission, empty = reader.payload['submissions']
        row = submission['rows'][0]
        self.assertEqual(row['spread'], 0)
        self.assertTrue(row['different'])
        self.assertIsNone(row['candidates']['grader_C'])
        self.assertIsNone(row['proposal'])
        self.assertIsNone(submission['members'][0]['totals']['grader_D'])
        self.assertIsNone(submission['members'][0]['proposed'])
        self.assertIsNone(empty['spread'])
        self.assertFalse(submission['ready'])
        self.assertFalse(submission['approved'])
        self.assertIn('Missing grader_C', submission['flags'])
        self.assertTrue((self.root / 'report_assets/columns.js').is_file())
        self.assertTrue((self.root / 'report_assets/columns.css').is_file())
        with (self.root / 'report_assets/columns.js').open('a', encoding='utf-8') as handle:
            handle.write('\n// changed after preparation\n')
        self.run_cli('report', fail=True)

    def test_partial_report_and_invalid_records(self) -> None:
        self.run_cli('prepare')
        self.run_cli('report')
        self.assertIn(
            'Missing grader_A',
            (self.root / 'reports/review.html').read_text(encoding='utf-8'),
        )
        self.run_cli('prompt', '--role', 'arbiter', fail=True)
        record = cast(
            DraftEvaluation,
            json.loads(
                self.run_cli(
                    'template',
                    '--role',
                    'grader',
                    '--grader',
                    'grader_A',
                    '--student',
                    's01',
                )
            ),
        )
        for value in record['scores'].values():
            value.update({'score': True, 'feedback': 'A finding.', 'evidence': ['app.txt:1']})
        record['summary'] = 'Example.'
        write_json(self.root / 'evaluations/grader_A/s01.json', record)
        self.assertIn('out of range', self.run_cli('validate', fail=True))
        for value in record['scores'].values():
            value['score'] = 2
        record['scores']['unexpected'] = record['scores']['design']
        write_json(self.root / 'evaluations/grader_A/s01.json', record)
        self.assertIn('expected exactly', self.run_cli('validate', fail=True))
        (self.root / 'evaluations/grader_A/s01.json').unlink()
        write_json(self.root / 'evaluations/grader_A/intruder.json', {})
        self.assertIn('Unexpected evaluation', self.run_cli('validate', fail=True))

    def test_modified_source_and_inputs(self) -> None:
        self.run_cli('prepare')
        target = self.root / 'snapshots/s01/app.txt'
        original_source = target.read_bytes()
        target.write_text('tampered', encoding='utf-8')
        self.assertIn('snapshot modified', self.run_cli('validate', fail=True))
        target.write_bytes(original_source)
        self.run_cli('validate')
        (self.root / 'input/policy.md').write_text('Changed policy', encoding='utf-8')
        self.assertIn('Inputs changed', self.run_cli('validate', fail=True))

    def test_inclusive_cutoff_and_both_timestamps(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration['snapshot_policy']['boundary'] = 'inclusive'
        configuration['snapshot_policy']['timestamp'] = 'both'
        write_json(self.root / 'assignment.json', configuration)
        self.run_cli('prepare')
        manifest = cast(SourceManifest, read_json(self.root / 'manifest.json'))
        self.assertEqual(manifest['submissions']['s01']['commit'], self.commits['s01'][1])

    def test_explicit_missing_and_no_eligible_commit(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration['snapshot_policy']['mode'] = 'explicit'
        write_json(self.root / 'assignment.json', configuration)
        self.assertIn('requires a submitted commit', self.run_cli('prepare', fail=True))
        configuration['snapshot_policy'].update({'mode': 'deadline', 'deadline': '2020-01-01T00:00:00+00:00'})
        write_json(self.root / 'assignment.json', configuration)
        self.assertIn('no eligible commit', self.run_cli('prepare', fail=True))
        self.assertFalse((self.root / 'manifest.json').exists())

    def test_configurable_weights_with_unequal_scoring_scales(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        criteria = {criterion['key']: criterion for criterion in configuration['criteria']}
        criteria['correctness']['max_score'] = 6
        criteria['correctness']['weight'] = 75
        criteria['design']['max_score'] = 2
        criteria['design']['weight'] = 25
        write_json(self.root / 'assignment.json', configuration)
        self.run_cli('prepare')

        workspace = Workspace(self.root)
        record = workspace.template('s01', 'grader_A')
        record['summary'] = 'Fictional weighted example.'
        for criterion, assessment in record['scores'].items():
            assessment.update(
                {
                    'score': workspace.criteria[criterion]['max_score'],
                    'feedback': 'Reviewed the fictional submission.',
                    'evidence': ['app.txt'],
                }
            )
        manifest = workspace.prepared()
        self.assertEqual(
            workspace.totals(workspace.validate_record(record, 's01', 'grader_A', manifest), 's01'),
            (8, 100.0),
        )
        record['scores']['correctness']['score'] = 0
        self.assertEqual(
            workspace.totals(workspace.validate_record(record, 's01', 'grader_A', manifest), 's01'),
            (2, 25.0),
        )

    def test_archived_workspace_moves_and_explicit_commit(self) -> None:
        configuration = cast(Assignment, read_json(self.root / 'assignment.json'))
        configuration['snapshot_policy']['mode'] = 'explicit'
        write_json(self.root / 'assignment.json', configuration)
        record_path = self.root / 'input/roster.csv'
        with record_path.open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row['commit'] = self.commits[row['submission_id']][0]
        with record_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self.run_cli('prepare')
        moved = self.base / 'moved archive'
        self.root.rename(moved)
        self.root = moved
        self.run_cli('validate')
        self.run_cli('prepare')

    def test_duplicate_roster_and_csv_formula(self) -> None:
        record_path = self.root / 'input/roster.csv'
        rows = record_path.read_text(encoding='utf-8').splitlines()
        record_path.write_text('\n'.join(rows + [rows[1]]) + '\n', encoding='utf-8')
        self.assertIn('Duplicate student', self.run_cli('prepare', fail=True))
        self.assertIn("'  =1+1", csv_text([['  =1+1']]))


if __name__ == '__main__':
    unittest.main()
