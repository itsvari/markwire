"""Exercise shared setup settings without network or model access."""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture_json import read_fixture
from grading_core import GradingError, write_json
from grading_setup import (
    PREFERENCES_PATH,
    VERIFICATION_PATH,
    default_preferences,
    load_preferences,
    load_verification,
    prompt_preferences,
    report_requested,
    role_defaults,
    setup,
)

ROOT = Path(__file__).resolve().parents[1]
GRADER = 'grader_A'


class SetupTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'workspace'
        shutil.copytree(ROOT / 'template', self.root)
        roster = self.root / 'input' / 'roster.csv'
        roster.write_text(
            'submission_id,student_id,name,github_username,repo_url,branch,commit\n'
            'one,one,Example,example-one,./dummy,main,\n',
            encoding='utf-8',
        )

    def test_operator_preferences_select_role_without_exposing_parent_models(
        self,
    ) -> None:
        preferences = default_preferences()
        preferences['models'][GRADER] = {
            'provider': 'operator-provider',
            'model': 'operator-model',
        }
        preferences['roles']['grader'] = {
            **role_defaults(),
            'browser': True,
            'auth': 'human',
        }
        preferences['roles'][GRADER] = {
            **preferences['roles']['grader'],
            'subagents': 'required',
            'minimum_workers_per_submission': 1,
            'max_concurrent_workers': 2,
            'task_models': {'vision': {'provider': 'worker-provider', 'model': 'worker-model'}},
        }
        preferences['reports'] = {'mode': 'blockers', 'stages': []}
        write_json(self.root / PREFERENCES_PATH, preferences)
        loaded = load_preferences(self.root, (GRADER, 'grader_B'))
        assigned = prompt_preferences(loaded, 'grader', GRADER)
        self.assertIn('at least 1', assigned)
        self.assertIn('worker-provider/worker-model', assigned)
        self.assertNotIn('operator-provider', assigned)
        self.assertIn('human-assisted sign-in', assigned)
        self.assertIn('delegation is disabled', prompt_preferences(loaded, 'grader', 'grader_B'))
        self.assertTrue(report_requested(loaded, 'grading', blocker=True))
        self.assertFalse(report_requested(loaded, 'grading'))

    def test_invalid_verification_does_not_silently_drop_required_checks(self) -> None:
        check = {
            'id': 'workflow',
            'instructions': 'Exercise the main workflow.',
            'criteria': ['unknown'],
            'capabilities': ['browser'],
            'required': True,
        }
        write_json(self.root / VERIFICATION_PATH, {'version': 1, 'checks': [check]})
        with self.assertRaisesRegex(GradingError, 'unknown rubric criterion'):
            load_verification(self.root, ('correctness',))
        write_json(self.root / VERIFICATION_PATH, {'version': 1, 'checks': [check, check]})
        with self.assertRaisesRegex(GradingError, 'Duplicate verification check'):
            load_verification(self.root)

    def test_wizard_saves_reviewable_canonical_draft(self) -> None:
        def answer(prompt: str) -> str:
            if prompt.strip().startswith('Scope ('):
                return 'submission'
            if prompt.strip().startswith('Requires starter files'):
                return 'no'
            if prompt.strip().startswith('Git source selection'):
                return 'head'
            return ''

        output = io.StringIO()
        with (
            patch('builtins.input', side_effect=answer),
            contextlib.redirect_stdout(output),
        ):
            setup(self.root)
        self.assertIn('Worked grade', output.getvalue())
        self.assertIn('Draft saved', output.getvalue())
        self.assertFalse(read_fixture(self.root / 'assignment.json')['policy_confirmed'])
        self.assertEqual(load_preferences(self.root, (GRADER,))['orchestration'], 'manual')
        before = (self.root / 'assignment.json').read_bytes()
        with contextlib.redirect_stdout(output):
            setup(self.root, review_only=True)
        self.assertEqual(before, (self.root / 'assignment.json').read_bytes())
