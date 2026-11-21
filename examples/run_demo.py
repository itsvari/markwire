"""Generate a self-contained Markwire demo with fictional submissions and grades."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grading_core import (
    Assignment,
    DraftEvaluation,
    Verification,
    read_json,
    write_json,
)


SUBMISSIONS = {'demo01': ('Example Ada',), 'demo02': ('Example Sam', 'Example Jamie')}
GRADERS = ('grader_A', 'grader_B')
ASSESSORS = (*GRADERS, 'arbiter')
SHARED_HIGH_MARK = 3
SHARED_LOW_MARK = 2
INDIVIDUAL_HIGH_MARK = 2
INDIVIDUAL_LOW_MARK = 0


def command(*arguments: str | Path) -> str:
    return subprocess.check_output(
        [str(argument) for argument in arguments],
        encoding='utf-8',
    ).strip()


def create_source(repository: Path) -> None:
    repository.mkdir(parents=True)
    command('git', 'init', '-b', 'main', repository)
    command('git', '-C', repository, 'config', 'user.name', 'Fictional fixture')
    command('git', '-C', repository, 'config', 'user.email', 'demo@example.invalid')
    command('git', '-C', repository, 'config', 'commit.gpgsign', 'false')
    (repository / 'greeting.py').write_text(
        'def greet(name: str) -> str:\n    return f"Hello, {name}!"\n',
        encoding='utf-8',
        newline='\n',
    )
    command('git', '-C', repository, 'add', 'greeting.py')
    command('git', '-C', repository, 'commit', '-m', 'Implement a greeting')


def configure_assignment(destination: Path) -> None:
    configuration_path = destination / 'assignment.json'
    configuration = cast(Assignment, read_json(configuration_path))
    configuration.update(
        {
            'assignment_id': 'fictional-demo',
            'title': 'Fictional assignment review',
            'policy_confirmed': True,
            'baseline_required': False,
            'graders': list(GRADERS),
        }
    )
    configuration['snapshot_policy']['mode'] = 'head'
    for criterion in configuration['criteria']:
        criterion['scope'] = 'submission' if criterion['key'] == 'correctness' else 'individual'
    write_json(configuration_path, configuration)
    (destination / 'input/brief.md').write_text(
        '# Fictional assignment\n\n'
        'Implement a reusable `greet(name)` function that returns a formatted greeting for valid names.\n',
        encoding='utf-8',
    )
    (destination / 'input/policy.md').write_text(
        '# Demo policy\n\n'
        'Pin the branch head for this fictional workspace. Correctness is a shared submission criterion and design '
        'is an individual criterion. All evaluations, sanity checks, and approvals in this demo are synthetic fixtures.\n',
        encoding='utf-8',
    )


def write_roster(destination: Path) -> None:
    source_root = destination / 'demo-sources'
    with (destination / 'input/roster.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
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
        for submission_id, names in SUBMISSIONS.items():
            repository = source_root / submission_id
            create_source(repository)
            student_ids = (submission_id,) if len(names) == 1 else (submission_id, 'demo03')
            for student_id, name in zip(student_ids, names, strict=True):
                writer.writerow(
                    (
                        submission_id,
                        student_id,
                        name,
                        f'example-{student_id}',
                        repository,
                        'main',
                        '',
                    )
                )


def populate_evaluation(record: DraftEvaluation, author: str) -> None:
    groups = {'submission': record['scores'], **record['individual_scores']}
    for student_id, assessments in groups.items():
        for criterion, assessment in assessments.items():
            if student_id == 'demo03':
                score = INDIVIDUAL_LOW_MARK if author == 'grader_B' else INDIVIDUAL_HIGH_MARK
            else:
                score = SHARED_LOW_MARK if author == 'grader_B' else SHARED_HIGH_MARK
            feedback = (
                'Your greeting function returns the expected output for standard inputs. Document which names it '
                'accepts and handle empty or whitespace-only inputs explicitly.'
                if criterion == 'correctness'
                else 'Your function signature and naming are clear and concise. Add a brief docstring explaining '
                'its input contract and any assumptions.'
            )
            assessment.update({'score': score, 'feedback': feedback, 'evidence': ['greeting.py:1-2']})
            if author == 'arbiter':
                assessment['rationale'] = (
                    'Synthetic arbiter rationale: verified greeting.py and recorded a separate authored assessment '
                    'for each applicable scope.'
                )
    record['summary'] = (
        'Your implementation delivers a clear, readable greeting function for standard names. '
        'However, it does not specify or guard against blank inputs, leaving edge-case behaviour undefined. '
        'Next, document the accepted input contract and add boundary tests covering empty and whitespace inputs.'
    )


def create_demo(destination: Path) -> Path:
    command(sys.executable, ROOT / 'grading.py', 'init', destination)
    configure_assignment(destination)
    write_roster(destination)

    def grading_command(*arguments: str) -> str:
        return command(
            sys.executable,
            destination / 'grading.py',
            '--workspace',
            destination,
            *arguments,
        )

    grading_command('prepare')
    for submission_id in SUBMISSIONS:
        for author in ASSESSORS:
            role = 'arbiter' if author == 'arbiter' else 'grader'
            record = cast(
                DraftEvaluation,
                json.loads(
                    grading_command(
                        'template',
                        '--role',
                        role,
                        '--grader',
                        author,
                        '--submission',
                        submission_id,
                    )
                ),
            )
            populate_evaluation(record, author)
            write_json(destination / 'evaluations' / author / f'{submission_id}.json', record)
        verification = cast(
            Verification,
            json.loads(
                grading_command(
                    'template',
                    '--role',
                    'sanity',
                    '--submission',
                    submission_id,
                )
            ),
        )
        verification['checks'] = [
            {
                'claim': 'The greeting function is present and returns a greeting',
                'status': 'confirmed',
                'finding': 'Confirmed that greeting.py defines greet(name) in the synthetic fixture.',
                'evidence': ['greeting.py:1-2'],
            }
        ]
        write_json(destination / 'verification' / f'{submission_id}.json', verification)
        grading_command(
            'approve',
            '--submission',
            submission_id,
            '--reviewer',
            'Fictional reviewer',
            '--notes',
            'Synthetic approval generated for the offline demo.',
        )
    grading_command('export')
    return Path(grading_command('report'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='A new directory for the fictional workspace')
    arguments = parser.parse_args()
    print(create_demo(arguments.destination.expanduser().resolve()))


if __name__ == '__main__':
    main()
