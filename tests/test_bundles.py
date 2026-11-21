"""Scoped bundle round trip with an ordinary pinned Git submission."""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import cast
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grading import initialize
from grading_bundles import (
    BundleError,
    finish_bundle,
    import_bundle,
    imported_result,
    open_bundle,
    pack_bundle,
)
from fixture_json import fixture_objects, read_fixture
from grading_core import Assignment, Workspace, read_json, write_json
from grading_sources import prepare


class BundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def create_workspace(self, name: str) -> Path:
        workspace = self.scratch / name
        repository = self.scratch / f'{name}-source'
        self.git('init', '-b', 'main', str(repository))
        (repository / 'README.md').write_text('test application\n', encoding='utf-8')
        self.git('-C', str(repository), 'add', 'README.md')
        self.git(
            '-C',
            str(repository),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.invalid',
            'commit',
            '-m',
            'Pin the original submission',
        )
        initialize(workspace)
        config = cast(Assignment, read_json(workspace / 'assignment.json'))
        config.update({'policy_confirmed': True, 'baseline_required': False})
        config['snapshot_policy']['mode'] = 'head'
        for criterion in config['criteria']:
            criterion['scope'] = 'submission'
        write_json(workspace / 'assignment.json', config)
        with (workspace / 'input/roster.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    'submission_id',
                    'student_id',
                    'name',
                    'github_username',
                    'repo_url',
                    'branch',
                    'commit',
                )
            )
            writer.writerow(
                (
                    'team_1',
                    'student_1',
                    'Fixture Student',
                    'fixture',
                    repository,
                    'main',
                    '',
                )
            )
        prepare(Workspace(workspace))
        return workspace

    def git(self, *arguments: str) -> None:
        subprocess.run(['git', *arguments], check=True, capture_output=True)

    def task_specification(self, *, snapshot: bool = False) -> Path:
        specification = self.scratch / 'task-specification.json'
        write_json(
            specification,
            {
                'task_id': 'local-browser',
                'attempt': 1,
                'submission_id': 'team_1',
                'capabilities': ['browser'],
                'checks': [
                    {'id': 'sign-in', 'instructions': 'Try signing in.'},
                    {'id': 'sign-out', 'instructions': 'Try signing out.'},
                ],
                'files': ['input/policy.md'],
                'include_snapshot': snapshot,
            },
        )
        return specification

    def test_detached_round_trip_and_partial_receipt(self) -> None:
        workspace = self.create_workspace('main')
        archive = self.scratch / 'task.zip'
        self.workspace_cli(workspace, 'bundle', 'pack', self.task_specification(snapshot=True), archive)
        detached_tool = self.scratch / 'detached' / 'grading_bundles.py'
        detached_tool.parent.mkdir()
        shutil.copy2(ROOT / 'grading_bundles.py', detached_tool)
        worker = detached_tool.parent / 'worker'
        self.detached_cli(detached_tool, 'open', archive, worker)
        self.assertTrue((worker / 'inputs/input/policy.md').is_file())
        self.assertTrue((worker / 'inputs/snapshots/team_1/README.md').is_file())
        self.assertFalse((worker / 'inputs/snapshots/team_1/.git').exists())
        self.assertFalse((worker / 'inputs/input/roster.csv').exists())
        self.assertFalse((worker / 'evaluations').exists())
        draft = read_fixture(worker / 'result.json')
        draft['environment'] = 'Synthetic macOS browser fixture'
        fixture_objects(draft['observations'])[0].update(
            status='blocked',
            observation='Sign-in needs assistance',
            evidence=['evidence/access.txt'],
        )
        (worker / 'evidence/access.txt').write_text('Access blocked', encoding='utf-8')
        (worker / 'unrelated.log').write_text('Other work', encoding='utf-8')
        write_json(worker / 'result.json', draft)
        output = self.scratch / 'result.zip'
        self.detached_cli(detached_tool, 'finish', worker, output)
        with zipfile.ZipFile(output) as result_archive:
            self.assertEqual(set(result_archive.namelist()), {'result.json', 'evidence/access.txt'})
        self.assertIsNone(imported_result(workspace, 'local-browser', 1))
        receipt = Path(self.workspace_cli(workspace, 'bundle', 'import', output))
        workspace_alias = workspace / '..' / workspace.name
        self.assertEqual(import_bundle(workspace_alias, output), receipt)
        self.assertEqual(
            fixture_objects(read_fixture(receipt.parent / 'result.json')['observations'])[1]['status'],
            'untested',
        )
        self.assertEqual(
            (receipt.parent / 'evidence/access.txt').read_text(encoding='utf-8'),
            'Access blocked',
        )
        self.assertEqual(list((workspace / 'evaluations').rglob('*.json')), [])
        self.assertIsNotNone(imported_result(workspace, 'local-browser', 1))
        (receipt.parent / 'evidence/access.txt').write_text('Altered evidence', encoding='utf-8')
        with self.assertRaisesRegex(BundleError, 'no longer matches its receipt'):
            imported_result(workspace, 'local-browser', 1)

    def test_scoped_task_can_return_declared_output(self) -> None:
        workspace = self.create_workspace('main')
        specification = self.task_specification()
        specification_data = read_fixture(specification)
        specification_data['outputs'] = ['outputs/assessment.json']
        write_json(specification, specification_data)
        task_archive = pack_bundle(workspace, specification, self.scratch / 'task.zip')
        worker = open_bundle(task_archive, self.scratch / 'worker')
        self.assertIn('outputs/assessment.json', (worker / 'TASK.md').read_text(encoding='utf-8'))
        draft = read_fixture(worker / 'result.json')
        draft['environment'] = 'Fixture environment'
        for observation in fixture_objects(draft['observations']):
            observation.update({'status': 'passed', 'observation': 'Observed expected behaviour'})
        write_json(worker / 'result.json', draft)
        output = self.scratch / 'result.zip'
        with self.assertRaisesRegex(BundleError, 'Completed checks require all declared output files'):
            finish_bundle(worker, output)
        write_json(worker / 'outputs/assessment.json', {'score': 3})
        write_json(worker / 'outputs/other.json', {'score': 5})
        finish_bundle(worker, output)
        receipt = import_bundle(workspace, output)
        self.assertEqual(read_json(receipt.parent / 'outputs/assessment.json'), {'score': 3})
        self.assertFalse((receipt.parent / 'outputs/other.json').exists())
        self.assertEqual(list((workspace / 'evaluations').rglob('*.json')), [])

    def detached_cli(self, launcher: Path, command: str, source: Path, destination: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                '-I',
                str(launcher),
                command,
                str(source),
                str(destination),
            ],
            cwd=launcher.parent,
            capture_output=True,
            encoding='utf-8',
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def workspace_cli(self, workspace: Path, *arguments: str | Path) -> str:
        result = subprocess.run(
            [
                sys.executable,
                str(workspace / 'grading.py'),
                '--workspace',
                str(workspace),
                *map(str, arguments),
            ],
            capture_output=True,
            encoding='utf-8',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_archive_rejects_traversal_and_undeclared_payload(self) -> None:
        workspace = self.create_workspace('main')
        original = pack_bundle(workspace, self.task_specification(), self.scratch / 'task.zip')
        for entry, error in (
            ('../escaped.txt', 'Unsafe bundle path'),
            ('inputs/extra', 'declared files'),
        ):
            with self.subTest(entry=entry):
                archive_path = self.scratch / 'tampered.zip'
                shutil.copy2(original, archive_path)
                with zipfile.ZipFile(archive_path, 'a') as archive:
                    archive.writestr(entry, 'added bytes')
                destination = self.scratch / 'worker'
                with self.assertRaisesRegex(BundleError, error):
                    open_bundle(archive_path, destination)
                self.assertFalse(destination.exists())
                self.assertFalse((self.scratch / 'escaped.txt').exists())

    def test_import_rejects_foreign_revision_and_changed_input(self) -> None:
        workspace = self.create_workspace('main')
        other_workspace = self.create_workspace('other')
        specification = self.task_specification()
        task_archive = pack_bundle(workspace, specification, self.scratch / 'task.zip')
        pack_bundle(other_workspace, specification, self.scratch / 'other.zip')
        worker = open_bundle(task_archive, self.scratch / 'worker')
        draft = read_fixture(worker / 'result.json')
        draft['environment'] = 'Fixture environment'
        write_json(worker / 'result.json', draft)
        output = finish_bundle(worker, self.scratch / 'result.zip')
        with self.assertRaisesRegex(BundleError, 'stale workspace revision'):
            import_bundle(other_workspace, output)
        (worker / 'inputs/input/policy.md').write_text('Incorrect policy', encoding='utf-8')
        with self.assertRaisesRegex(BundleError, 'original task inputs have changed'):
            finish_bundle(worker, self.scratch / 'tampered.zip')


if __name__ == '__main__':
    unittest.main()
