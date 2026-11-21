"""Check T3 planning, retries, stage gates and detached probe ingestion locally."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import cast

import test_workflow
from fixture_json import fixture_objects, parse_fixture, read_fixture
from grading_core import DraftEvaluation, write_json
from grading_setup import PREFERENCES_PATH, default_preferences, role_defaults


class OrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_workflow.WorkflowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.run_cli('prepare')
        preferences = default_preferences()
        preferences['orchestration'] = 't3'
        preferences['models'] = {
            role: {'provider': f'fixture-{role}', 'model': f'fixture-model-{role}'}
            for role in ('grader_A', 'grader_B', 'arbiter', 'sanity')
        }
        preferences['roles'] = {'grader': {**role_defaults(), 'browser': True}}
        preferences['machines'] = {'mac': {'environment_id': 'fixture-mac', 'capabilities': ['browser']}}
        write_json(self.root / PREFERENCES_PATH, preferences)

    def runner(self, *arguments: str | Path, fail: bool = False) -> dict[str, object]:
        command = [sys.executable, 'grading_orchestration.py', *map(str, arguments)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, encoding='utf-8')
        if fail:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            return {'error': result.stderr}
        self.assertEqual(result.returncode, 0, result.stderr)
        return parse_fixture(result.stdout)

    def evaluation(self, role: str, submission: str) -> None:
        record = cast(
            DraftEvaluation,
            json.loads(
                self.fixture.run_cli(
                    'template',
                    '--role',
                    'arbiter' if role == 'arbiter' else 'grader',
                    '--grader',
                    role,
                    '--submission',
                    submission,
                )
            ),
        )
        for assessment in record['scores'].values():
            assessment.update({'score': 2, 'feedback': 'Reviewed the fixture.', 'evidence': ['app.txt:1']})
            if role == 'arbiter':
                assessment['rationale'] = 'Both graders identified the same behaviour.'
        record['summary'] = 'The fixture grade is supported by source evidence.'
        write_json(self.root / 'evaluations' / role / f'{submission}.json', record)

    def test_retries_preserve_completed_submissions_and_gates(self) -> None:
        started = self.runner('start', 'round-one')
        self.assertEqual(started['stage'], 'grading')
        self.assertIn(
            'outside the runnable stage',
            str(self.runner('reserve', 'round-one', 'arbiter', fail=True)['error']),
        )
        reservation = self.runner('reserve', 'round-one', 'grader_A')
        self.assertEqual(reservation, self.runner('reserve', 'round-one', 'grader_A'))
        request_id = str(reservation['clientRequestId'])
        self.runner(
            'attach',
            'round-one',
            'grader_A',
            '--request-id',
            request_id,
            '--task-id',
            'task-1',
            '--thread-id',
            'thread-1',
        )
        self.evaluation('grader_A', 's01')
        failed = self.runner(
            'finish',
            'round-one',
            'grader_A',
            '--request-id',
            request_id,
            '--status',
            'completed',
            fail=True,
        )
        self.assertIn('validated outputs are still missing', str(failed['error']))
        self.runner(
            'finish',
            'round-one',
            'grader_A',
            '--request-id',
            request_id,
            '--status',
            'failed',
        )
        retry = self.runner('reserve', 'round-one', 'grader_A', '--retry')
        self.assertNotEqual(retry['clientRequestId'], request_id)
        self.assertEqual(retry['submission_ids'], ['s02'])
        response = self.runner(
            'finish',
            'round-one',
            'grader_A',
            '--request-id',
            request_id,
            '--status',
            'failed',
            fail=True,
        )
        self.assertIn('different attempt', str(response['error']))
        self.evaluation('grader_A', 's02')
        self.evaluation('grader_B', 's01')
        self.evaluation('grader_B', 's02')
        self.assertEqual(self.runner('status', 'round-one')['stage'], 'grading')
        self.runner(
            'finish',
            'round-one',
            'grader_A',
            '--request-id',
            str(retry['clientRequestId']),
            '--status',
            'completed',
        )
        arbitration = self.runner('reserve', 'round-one', 'arbiter')
        self.assertEqual(arbitration['submission_ids'], ['s01', 's02'])
        self.evaluation('arbiter', 's01')
        self.evaluation('arbiter', 's02')
        self.runner(
            'finish',
            'round-one',
            'arbiter',
            '--request-id',
            str(arbitration['clientRequestId']),
            '--status',
            'completed',
        )
        sanity = self.runner('reserve', 'round-one', 'sanity')
        self.fixture.verify('s01')
        self.fixture.verify('s02')
        review = self.runner(
            'finish',
            'round-one',
            'sanity',
            '--request-id',
            str(sanity['clientRequestId']),
            '--status',
            'completed',
        )
        self.assertEqual((review['stage'], review['unresolved_submissions']), ('human_review', []))
        self.assertFalse(list((self.root / 'review').glob('*.json')))

    def test_linked_probe_waits_for_import_and_delegate_settlement(self) -> None:
        self.runner('start', 'remote-round', '--submission', 's01')
        specification = self.root / 'private' / 'probe.json'
        write_json(
            specification,
            {
                'task_id': 'browser-probe',
                'attempt': 1,
                'submission_id': 's01',
                'capabilities': ['browser'],
                'checks': [{'id': 'page', 'instructions': 'Inspect fixture page.'}],
            },
        )
        queued = self.runner(
            'add-probe',
            'remote-round',
            '--spec',
            specification,
            '--machine',
            'mac',
            '--model-role',
            'grader_A',
            '--before',
            'arbitration',
        )
        self.assertEqual(queued['stage'], 'grading')
        queued_tasks = cast(list[dict[str, object]], queued['tasks'])
        self.assertEqual(
            [task['slot'] for task in queued_tasks],
            ['grader_A', 'grader_B', 'probe-browser-probe-1'],
        )
        probe = self.runner('reserve', 'remote-round', 'probe-browser-probe-1')
        self.assertEqual(
            probe['target'],
            {
                'providerInstanceId': 'fixture-grader_A',
                'model': 'fixture-model-grader_A',
                'environmentId': 'fixture-mac',
            },
        )
        self.assertEqual(probe['dispatch'], 't3')
        request_id = str(probe['clientRequestId'])
        self.runner(
            'attach',
            'remote-round',
            'probe-browser-probe-1',
            '--request-id',
            request_id,
            '--task-id',
            'fixture-remote-task',
            '--thread-id',
            'fixture-remote-thread',
        )
        archive = self.root / 'private' / 'probe.zip'
        detached = self.fixture.base / 'mac-scratch'
        result = self.root / 'private' / 'result.zip'
        self.fixture.run_cli('bundle', 'pack', specification, archive)
        self.fixture.checked_command(sys.executable, self.root / 'grading_bundles.py', 'open', archive, detached)
        draft = read_fixture(detached / 'result.json')
        draft['environment'] = 'Fixture macOS browser'
        fixture_objects(draft['observations'])[0].update(
            status='passed',
            observation='Fixture page was visible.',
            evidence=['evidence/observation.txt'],
        )
        (detached / 'evidence' / 'observation.txt').write_text('Fixture transcript\n', encoding='utf-8')
        write_json(detached / 'result.json', draft)
        self.fixture.checked_command(sys.executable, self.root / 'grading_bundles.py', 'finish', detached, result)
        self.fixture.run_cli('bundle', 'import', result)
        self.evaluation('grader_A', 's01')
        self.evaluation('grader_B', 's01')
        waiting = self.runner('status', 'remote-round')
        waiting_tasks = cast(list[dict[str, object]], waiting['tasks'])
        self.assertEqual([task['slot'] for task in waiting_tasks], ['probe-browser-probe-1'])
        settled = self.runner(
            'finish',
            'remote-round',
            'probe-browser-probe-1',
            '--request-id',
            request_id,
            '--status',
            'completed',
        )
        settled_tasks = cast(list[dict[str, object]], settled['tasks'])
        self.assertEqual([task['slot'] for task in settled_tasks], ['arbiter'])
