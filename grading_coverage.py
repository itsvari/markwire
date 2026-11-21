"""Independent verification records and coverage gates."""

from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Literal, TypedDict, cast

from grading_core import (
    Workspace,
    digest,
    exact_keys,
    json_object,
    nonempty,
    read_json,
    require,
    strings,
)
from grading_setup import load_verification

CoverageStatus = Literal['passed', 'failed', 'blocked', 'untested']
COVERAGE_STATUSES = ('passed', 'failed', 'blocked', 'untested')
EVIDENCE_REQUIRED_STATUSES = ('passed', 'failed')


class Observation(TypedDict):
    check_id: str
    status: CoverageStatus
    role: str
    environment: str
    observed_at: str
    expected: str
    observation: str
    evidence: list[str]


class Coverage(TypedDict):
    submission_id: str
    grader: str
    source_digest: str
    checklist_digest: str
    observations: list[Observation]


def validate_checklist(workspace: Workspace) -> None:
    load_verification(workspace.root, list(workspace.criteria))


def coverage_record_path(root: Path, grader: str, submission_id: str) -> Path:
    return root / 'coverage' / grader / f'{submission_id}.json'


def coverage_template(workspace: Workspace, grader: str, submission_id: str) -> Coverage:
    require(grader in workspace.config['graders'], 'Select a configured grader alias')
    require(submission_id in workspace.roster, f'Unknown submission {submission_id}')
    manifest = workspace.prepared()
    checks = load_verification(workspace.root)
    source_digest = manifest['submissions'][submission_id]['commit']
    checklist_digest = digest(checks)
    previous: dict[str, Observation] = {}
    path = coverage_record_path(workspace.root, grader, submission_id)
    if path.exists():
        record, _ = validate_coverage(
            path, grader, submission_id, source_digest, checklist_digest, {check['id'] for check in checks}
        )
        previous = {observation['check_id']: observation for observation in record['observations']}
    return {
        'submission_id': submission_id,
        'grader': grader,
        'source_digest': source_digest,
        'checklist_digest': checklist_digest,
        'observations': [
            previous.get(
                check['id'],
                {
                    'check_id': check['id'],
                    'status': 'untested',
                    'role': '',
                    'environment': '',
                    'observed_at': '',
                    'expected': '',
                    'observation': '',
                    'evidence': [],
                },
            )
            for check in checks
        ],
    }


def read_coverage(
    workspace: Workspace,
    submission_id: str,
    source_digest: str,
) -> tuple[dict[str, Coverage], list[str]]:
    checks = load_verification(workspace.root)
    if not checks:
        return {}, []
    checklist_digest = digest(checks)
    check_identifiers = {check['id'] for check in checks}
    required_check_identifiers = {check['id'] for check in checks if check['required']}
    records: dict[str, Coverage] = {}
    incomplete: list[str] = []
    for grader in workspace.config['graders']:
        path = coverage_record_path(workspace.root, grader, submission_id)
        if not path.exists():
            if required_check_identifiers:
                incomplete.append(grader)
            continue
        records[grader], observed_checks = validate_coverage(
            path,
            grader,
            submission_id,
            source_digest,
            checklist_digest,
            check_identifiers,
        )
        if required_check_identifiers - observed_checks:
            incomplete.append(grader)
    return records, incomplete


def validate_coverage(
    path: Path,
    grader: str,
    submission_id: str,
    source_digest: str,
    checklist_digest: str,
    check_identifiers: AbstractSet[str],
) -> tuple[Coverage, set[str]]:
    record = json_object(read_json(path), str(path))
    exact_keys(record, Coverage.__annotations__, str(path))
    require(
        record['grader'] == grader and record['submission_id'] == submission_id, f'{path}: coverage identity mismatch'
    )
    require(
        record['source_digest'] == source_digest and record['checklist_digest'] == checklist_digest,
        f'{path}: stale coverage input',
    )
    observations = record['observations']
    require(isinstance(observations, list), f'{path}: coverage observations must be a list')
    observed_checks: set[str] = set()
    for observation_value in cast(list[object], observations):
        observation = json_object(observation_value, str(path))
        exact_keys(observation, Observation.__annotations__, str(path))
        identifier = nonempty(observation['check_id'], f'{path} check ID')
        require(identifier in check_identifiers - observed_checks, f'{path}: unknown or repeated check')
        status = observation['status']
        require(status in COVERAGE_STATUSES, f'{path}: invalid coverage status')
        for field in ('role', 'environment', 'observed_at', 'expected', 'observation'):
            nonempty(observation[field], f'{path} {identifier} {field}')
        minimum_evidence_count = int(status in EVIDENCE_REQUIRED_STATUSES)
        strings(observation['evidence'], f'{path} {identifier} evidence', minimum_evidence_count)
        observed_checks.add(identifier)
    return cast(Coverage, record), observed_checks
