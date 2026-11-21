"""Assignment contracts, immutable inputs, review records and scoring."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from grading_coverage import Coverage


ENGINE_FILES = (
    'grading.py',
    'grading_core.py',
    'grading_sources.py',
    'grading_reports.py',
    'grading_setup.py',
    'grading_revisions.py',
    'grading_bundles.py',
    'grading_stage_reports.py',
    'grading_orchestration.py',
    'grading_coverage.py',
)
MAX_ROUNDING_DIGITS = 6
DEFAULT_ROUNDING_DIGITS = 1
PERCENTAGE_SCALE = 100
ATOMIC_WRITE_SUFFIX = '.markwire-tmp'
GITHUB_USERNAME_PATTERN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
GIT_COMMIT_PATTERN = re.compile(r'[0-9a-fA-F]{40}')
ROSTER_COLUMNS = ('submission_id', 'student_id', 'name', 'github_username', 'repo_url', 'branch', 'commit')
REQUIRED_CRITERION_FIELDS = ('key', 'label', 'max_score', 'weight', 'scope', 'guidance')
ASSESSMENT_FIELDS = ('score', 'feedback', 'evidence')
ASSESSMENT_SCOPES = ('submission', 'individual', 'unresolved')
SOURCE_MODES = ('explicit', 'deadline', 'head')
SANITY_STATUSES = ('confirmed', 'refuted', 'unverified')
Mark = int | float


class GradingError(ValueError):
    pass


class Criterion(TypedDict):
    key: str
    label: str
    max_score: int
    weight: Mark
    scope: str
    guidance: str
    allowed_scores: NotRequired[list[Mark]]


class SnapshotPolicy(TypedDict):
    mode: str
    deadline: str | None
    boundary: str
    timestamp: str


class Assignment(TypedDict):
    assignment_id: str
    title: str
    policy_confirmed: bool
    graders: list[str]
    criteria: list[Criterion]
    snapshot_policy: SnapshotPolicy
    review_thresholds: dict[str, Mark]
    baseline_required: bool
    rounding_digits: NotRequired[int]


class Member(TypedDict):
    student_id: str
    name: str
    github_username: str


class Submission(TypedDict):
    submission_id: str
    members: list[Member]
    repo_url: str
    branch: str
    commit: str


class Snapshot(TypedDict):
    commit: str
    tree: str
    branch: str
    branch_head: str
    author_time: str
    committer_time: str
    excluded_commits: list[str]
    repo_url: str
    members: list[Member]
    kind: NotRequired[Literal['local']]
    executable_files: NotRequired[list[str] | None]


class SnapshotCollection(TypedDict):
    submissions: dict[str, Snapshot]


class SourceManifest(SnapshotCollection):
    version: int
    inputs_digest: str
    created_at: str
    snapshot_policy: SnapshotPolicy


class Assessment(TypedDict):
    score: Mark
    feedback: str
    evidence: list[str]
    rationale: NotRequired[str]


class DraftAssessment(TypedDict):
    score: Mark | None
    feedback: str
    evidence: list[str]
    rationale: NotRequired[str]


class Evaluation(TypedDict):
    submission_id: str
    author: str
    manifest_digest: str
    commit: str
    scores: dict[str, Assessment]
    individual_scores: dict[str, dict[str, Assessment]]
    summary: str
    flags: list[str]


class DraftEvaluation(TypedDict):
    submission_id: str
    author: str
    manifest_digest: str
    commit: str
    scores: dict[str, DraftAssessment]
    individual_scores: dict[str, dict[str, DraftAssessment]]
    summary: str
    flags: list[str]


class Correction(TypedDict):
    parent_digest: str
    reason: str
    evidence: list[str]
    replacement: Evaluation


class SanityCheck(TypedDict):
    claim: str
    status: Literal['confirmed', 'refuted', 'unverified']
    finding: str
    evidence: list[str]
    grade_affecting: NotRequired[bool]


class Verification(TypedDict):
    submission_id: str
    proposal_digest: str
    checks: list[SanityCheck]
    unresolved: list[str]


class Approval(TypedDict):
    submission_id: str
    bundle_digest: str
    reviewer: str
    reviewed_at: str
    notes: str


class ReviewItem(TypedDict):
    submission: Submission
    evaluations: dict[str, Evaluation]
    proposal: Evaluation | None
    history: list[Correction]
    proposal_digest: str
    verification: Verification | None
    verification_current: bool
    bundle_digest: str
    missing: list[str]
    coverage: dict[str, Coverage]
    missing_coverage: list[str]
    ready: bool
    approved: bool


ReviewState = dict[str, ReviewItem]


def require(condition: object, message: str) -> None:
    if not condition:
        raise GradingError(message)


def read_json(path: str | Path) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, f'{path}: duplicate JSON key {key}')
            result[key] = value
        return result

    def invalid_number(number: str) -> None:
        raise GradingError(f'Invalid number {number}')

    decoded: object = json.loads(
        Path(path).read_text(encoding='utf-8'),
        object_pairs_hook=object_pairs,
        parse_constant=invalid_number,
    )
    return decoded


def digest(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def file_digest(path: str | Path) -> str:
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path: str | Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def write_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        newline='',
        dir=destination.parent,
        suffix=ATOMIC_WRITE_SUFFIX,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        try:
            handle.write(text)
            handle.close()
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)


def record_paths(folder: Path) -> Iterator[Path]:
    return (path for path in folder.glob('*') if path.suffix != ATOMIC_WRITE_SUFFIX)


def identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
        raise GradingError(f'{label} must contain only letters, numbers, underscores or hyphens')
    return value


def nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GradingError(f'{label} must be non-empty text')
    return value


def json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise GradingError(f'{label}: expected an object')
    return cast(dict[str, object], value)


def exact_keys(value: object, keys: Iterable[str], label: str) -> dict[str, object]:
    expected = tuple(keys)
    if not isinstance(value, dict) or value.keys() != set(expected):
        raise GradingError(f'{label}: expected exactly {", ".join(expected)}')
    return cast(dict[str, object], value)


def fields_with_optional(
    value: object,
    required: Iterable[str],
    optional: Iterable[str],
    label: str,
) -> dict[str, object]:
    fields = json_object(value, label)
    return exact_keys(fields, (*required, *(field for field in optional if field in fields)), label)


def json_array(value: object, label: str, minimum: int = 0) -> list[object]:
    if not isinstance(value, list) or len(value) < minimum:
        unit = 'item' if minimum == 1 else 'items'
        raise GradingError(f'{label}: expected a list with at least {minimum} {unit}')
    return cast(list[object], value)


def strings(value: object, label: str, minimum: int = 0) -> list[str]:
    return [nonempty(item, label) for item in json_array(value, label, minimum)]


def match_values(value: object, choices: Iterable[str], message: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise GradingError(message)
    return value


def positive_number(value: object, message: str) -> Mark:
    if type(value) is int and value > 0:
        return value
    if type(value) is float and value > 0 and math.isfinite(value):
        return value
    raise GradingError(message)


def input_hashes(root: Path) -> dict[str, str]:
    files = [
        root / name
        for name in ('assignment.json', 'GRADING_MANUAL.md', 'AGENTS.md', 'revision/revision.json', *ENGINE_FILES)
    ]
    files.extend(sorted((root / 'input').rglob('*')))
    files.extend(sorted((root / 'report_assets').rglob('*')))
    hashes: dict[str, str] = {}
    for path in files:
        relative = path.relative_to(root)
        if '.git' in relative.parts or path.name == '.DS_Store':
            continue
        require(not path.is_symlink(), f'Input symlinks are unsupported: {relative}')
        if path.is_file():
            hashes[relative.as_posix()] = file_digest(path)
    return hashes


def inputs_digest(root: Path) -> str:
    return digest(input_hashes(root))


def validate_inputs_digest(root: Path, expected: str) -> None:
    hashes = input_hashes(root)
    if expected == digest(hashes):
        return
    # older Windows workspaces hashed native path separators
    legacy_windows_hashes = {name.replace('/', '\\'): checksum for name, checksum in hashes.items()}
    require(
        expected == digest(legacy_windows_hashes), 'Inputs changed after preparation. Create a new workspace revision.'
    )


def parse_criterion(value: object) -> Criterion:
    fields = fields_with_optional(value, REQUIRED_CRITERION_FIELDS, ('allowed_scores',), 'criterion')
    maximum = fields['max_score']
    if type(maximum) is not int or maximum <= 0:
        raise GradingError('max_score must be a positive integer')
    criterion: Criterion = {
        'key': identifier(fields['key'], 'criterion key'),
        'label': nonempty(fields['label'], 'criterion label'),
        'guidance': nonempty(fields['guidance'], 'criterion guidance'),
        'scope': match_values(
            fields['scope'], ASSESSMENT_SCOPES, 'Criterion scope must be submission, individual or unresolved'
        ),
        'max_score': maximum,
        'weight': positive_number(fields['weight'], 'weight must be positive and finite'),
    }
    if 'allowed_scores' in fields:
        criterion['allowed_scores'] = parse_score_scale(fields['allowed_scores'], maximum)
    return criterion


def parse_score_scale(value: object, maximum: int) -> list[Mark]:
    message = 'allowed_scores must be unique, ascending numbers within the criterion scale'
    if not isinstance(value, list) or not value:
        raise GradingError(message)
    scores: list[Mark] = []
    for score in cast(list[object], value):
        if type(score) not in (int, float):
            raise GradingError(message)
        mark = cast(Mark, score)
        require(0 <= mark <= maximum and (not scores or mark > scores[-1]), message)
        scores.append(mark)
    require(scores[-1] == maximum, 'allowed_scores must include max_score')
    return scores


def parse_snapshot_policy(value: object) -> SnapshotPolicy:
    fields = exact_keys(value, SnapshotPolicy.__annotations__, 'snapshot_policy')
    mode = match_values(fields['mode'], SOURCE_MODES, 'snapshot mode must be explicit, deadline or head')
    deadline = fields['deadline']
    if deadline is not None and not isinstance(deadline, str):
        raise GradingError('Deadline must be an ISO date and time')
    if mode == 'deadline':
        require(
            deadline is not None and datetime.fromisoformat(deadline).tzinfo is not None,
            'Deadline must include a time zone offset',
        )
    return {
        'mode': mode,
        'deadline': deadline,
        'boundary': match_values(fields['boundary'], ('exclusive', 'inclusive'), 'Invalid cut-off boundary'),
        'timestamp': match_values(fields['timestamp'], ('committer', 'both'), 'Invalid timestamp convention'),
    }


def parse_assignment(value: object) -> Assignment:
    required = (key for key in Assignment.__annotations__ if key != 'rounding_digits')
    fields = fields_with_optional(value, required, ('rounding_digits',), 'assignment.json')
    rounding_digits = fields.get('rounding_digits', DEFAULT_ROUNDING_DIGITS)
    if type(rounding_digits) is not int or not 0 <= rounding_digits <= MAX_ROUNDING_DIGITS:
        raise GradingError(f'rounding_digits must be an integer from 0 to {MAX_ROUNDING_DIGITS}')
    policy_confirmed = fields['policy_confirmed']
    baseline_required = fields['baseline_required']
    if type(policy_confirmed) is not bool or type(baseline_required) is not bool:
        raise GradingError('Policy switches must be booleans')
    graders = strings(fields['graders'], 'graders', 1)
    require(
        all(re.fullmatch(r'grader_[A-Z][A-Z0-9]*', grader) for grader in graders),
        'Use anonymous aliases such as grader_A',
    )
    require(len(set(graders)) == len(graders), 'Duplicate graders')
    criteria = [parse_criterion(criterion) for criterion in json_array(fields['criteria'], 'criteria', 1)]
    require(len({criterion['key'] for criterion in criteria}) == len(criteria), 'Duplicate criterion keys')
    thresholds = fields_with_optional(
        fields['review_thresholds'],
        ('criterion_spread', 'total_spread'),
        ('weighted_percentage_points',),
        'review_thresholds',
    )
    assignment: Assignment = {
        'assignment_id': identifier(fields['assignment_id'], 'assignment_id'),
        'title': nonempty(fields['title'], 'title'),
        'policy_confirmed': policy_confirmed,
        'graders': graders,
        'criteria': criteria,
        'snapshot_policy': parse_snapshot_policy(fields['snapshot_policy']),
        'review_thresholds': {
            key: positive_number(number, 'Review thresholds must be positive') for key, number in thresholds.items()
        },
        'baseline_required': baseline_required,
    }
    if 'rounding_digits' in fields:
        assignment['rounding_digits'] = rounding_digits
    return assignment


def roster_row(value: dict[str, str]) -> dict[str, str]:
    require(None not in value and all(column is not None for column in value.values()), 'Malformed roster row')
    return {key: column.strip() for key, column in value.items()}


def load_roster(path: Path) -> dict[str, Submission]:
    roster: dict[str, Submission] = {}
    students: set[str] = set()
    accounts: set[str] = set()
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames == list(ROSTER_COLUMNS), 'Unexpected roster headers')
        for row in reader:
            record = roster_row(row)
            submission_id = identifier(record['submission_id'], 'submission_id')
            student_id = identifier(record['student_id'], 'student_id')
            require(student_id not in students, f'Duplicate student {student_id}')
            students.add(student_id)
            username = record['github_username']
            if username:
                require(
                    GITHUB_USERNAME_PATTERN.fullmatch(username),
                    f'{submission_id}: github_username must be a GitHub username without @',
                )
                require(username.lower() not in accounts, f'Duplicate GitHub account {username}')
                accounts.add(username.lower())
            member: Member = {
                'student_id': student_id,
                'name': nonempty(record['name'], f'{submission_id} name'),
                'github_username': username,
            }
            append_submission_member(roster, submission_id, record, member)
    return roster


def append_submission_member(
    roster: dict[str, Submission],
    submission_id: str,
    record: dict[str, str],
    member: Member,
) -> None:
    source = nonempty(record['repo_url'], f'{submission_id} repo_url')
    branch = record['branch']
    commit = record['commit'].lower()
    if commit:
        require(branch, f'{submission_id}: a local directory or ZIP uses a blank branch and commit')
        require(GIT_COMMIT_PATTERN.fullmatch(commit), f'{submission_id}: commit must be a full SHA-1')
    entry = roster.get(submission_id)
    if entry is None:
        roster[submission_id] = {
            'submission_id': submission_id,
            'members': [member],
            'repo_url': source,
            'branch': branch,
            'commit': commit,
        }
        return
    require(
        (entry['repo_url'], entry['branch'], entry['commit']) == (source, branch, commit),
        f'{submission_id}: members must share repo_url, branch and commit',
    )
    entry['members'].append(member)


def parse_manifest(value: object) -> SourceManifest:
    fields = exact_keys(value, SourceManifest.__annotations__, 'manifest.json')
    version = fields['version']
    if type(version) is not int:
        raise GradingError('manifest.json: invalid version')
    submissions = json_object(fields['submissions'], 'manifest submissions')
    return {
        'version': version,
        'inputs_digest': nonempty(fields['inputs_digest'], 'manifest input digest'),
        'created_at': nonempty(fields['created_at'], 'manifest creation time'),
        'snapshot_policy': parse_snapshot_policy(fields['snapshot_policy']),
        'submissions': {
            identifier(key, 'submission_id'): parse_snapshot(entry, key) for key, entry in submissions.items()
        },
    }


def parse_snapshot(value: object, submission_id: str) -> Snapshot:
    optional = ('kind', 'executable_files')
    mandatory = (key for key in Snapshot.__annotations__ if key not in optional)
    fields = fields_with_optional(value, mandatory, optional, f'{submission_id} snapshot')
    for item in json_array(fields['members'], f'{submission_id} snapshot members'):
        record = exact_keys(item, Member.__annotations__, 'snapshot member')
        username = record['github_username']
        if not isinstance(username, str):
            raise GradingError('snapshot member GitHub username must be text')
        identifier(record['student_id'], 'student_id')
        nonempty(record['name'], 'student name')
    for name in ('commit', 'tree', 'branch', 'branch_head', 'author_time', 'committer_time', 'repo_url'):
        require(isinstance(fields[name], str), f'{submission_id}: snapshot {name} must be text')
    if 'kind' in fields:
        require(fields['kind'] == 'local', f'{submission_id}: invalid snapshot kind')
    if fields.get('executable_files') is not None:
        strings(fields['executable_files'], f'{submission_id} executable files')
    strings(fields['excluded_commits'], f'{submission_id} excluded commits')
    return cast(Snapshot, fields)


def parse_approval(path: Path, submission_id: str) -> Approval:
    fields = exact_keys(read_json(path), Approval.__annotations__, str(path))
    require(fields['submission_id'] == submission_id, f'{submission_id}: approval identity mismatch')
    for field in ('bundle_digest', 'reviewer', 'reviewed_at', 'notes'):
        nonempty(fields[field], f'{submission_id} approval {field}')
    return cast(Approval, fields)


def parse_sanity_check(value: object, submission_id: str) -> SanityCheck:
    optional = ('grade_affecting',)
    mandatory = (key for key in SanityCheck.__annotations__ if key not in optional)
    fields = fields_with_optional(value, mandatory, optional, f'{submission_id} check')
    nonempty(fields['claim'], f'{submission_id} claim')
    nonempty(fields['finding'], f'{submission_id} finding')
    require(fields['status'] in SANITY_STATUSES, f'{submission_id}: invalid check status')
    require(type(fields.get('grade_affecting', True)) is bool, f'{submission_id}: grade_affecting must be boolean')
    strings(fields['evidence'], f'{submission_id} check evidence', 1)
    return cast(SanityCheck, fields)


def parse_verification(path: Path, submission_id: str) -> Verification:
    fields = exact_keys(read_json(path), Verification.__annotations__, str(path))
    require(fields['submission_id'] == submission_id, f'{submission_id}: verification identity mismatch')
    nonempty(fields['proposal_digest'], f'{submission_id} proposal digest')
    require(isinstance(fields['checks'], list) and fields['checks'], f'{submission_id}: verification needs checks')
    return {
        'submission_id': submission_id,
        'proposal_digest': cast(str, fields['proposal_digest']),
        'checks': [parse_sanity_check(check, submission_id) for check in json_array(fields['checks'], 'checks')],
        'unresolved': strings(fields['unresolved'], f'{submission_id} unresolved'),
    }


def object_schema(properties: Mapping[str, object]) -> dict[str, object]:
    return {'type': 'object', 'required': list(properties), 'properties': properties, 'additionalProperties': False}


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.config = parse_assignment(read_json(self.root / 'assignment.json'))
        self.criteria = {criterion['key']: criterion for criterion in self.config['criteria']}
        self.roster = load_roster(self.root / 'input' / 'roster.csv')

    def require_scopes(self) -> None:
        unresolved = self.criterion_keys('unresolved')
        require(not unresolved, f'Confirm submission or individual scope before preparation: {", ".join(unresolved)}')

    def criterion_keys(self, scope: str) -> list[str]:
        return [key for key, criterion in self.criteria.items() if criterion['scope'] == scope]

    def prepared(self) -> SourceManifest:
        require(self.config['policy_confirmed'], 'Read and confirm assignment policy before preparation')
        self.require_scopes()
        require(self.roster, 'Roster is empty')
        manifest = parse_manifest(read_json(self.root / 'manifest.json'))
        validate_inputs_digest(self.root, manifest['inputs_digest'])
        require(manifest['submissions'].keys() == self.roster.keys(), 'Manifest roster mismatch')
        for submission_id, submission in self.roster.items():
            require(
                manifest['submissions'][submission_id]['members'] == submission['members'],
                f'{submission_id}: manifest membership mismatch',
            )
        return manifest

    def assessments(self, record: Evaluation, student_id: str) -> dict[str, Assessment]:
        submission_id = record['submission_id']
        require(
            submission_id in self.roster
            and any(student_id == member['student_id'] for member in self.roster[submission_id]['members']),
            f'{submission_id}: unknown student {student_id}',
        )
        return {**record['scores'], **record['individual_scores'].get(student_id, {})}

    def totals(self, record: Evaluation, student_id: str) -> tuple[Mark, float]:
        assessments = self.assessments(record, student_id)
        raw_total = sum(assessments[key]['score'] for key in self.criteria)
        total_weight = sum(criterion['weight'] for criterion in self.criteria.values())
        weighted_total = sum(
            assessments[key]['score'] / criterion['max_score'] * criterion['weight']
            for key, criterion in self.criteria.items()
        )
        percentage = weighted_total / total_weight * PERCENTAGE_SCALE
        return raw_total, round(percentage, self.config.get('rounding_digits', DEFAULT_ROUNDING_DIGITS))

    def validate_record(
        self,
        value: object,
        submission_id: str,
        author: str,
        manifest: SourceManifest,
    ) -> Evaluation:
        record = exact_keys(value, Evaluation.__annotations__, 'evaluation')
        require(
            record['submission_id'] == submission_id and record['author'] == author,
            f'{submission_id}: identity or author mismatch',
        )
        require(record['manifest_digest'] == digest(manifest), f'{submission_id}: stale manifest reference')
        require(
            record['commit'] == manifest['submissions'][submission_id]['commit'],
            f'{submission_id}: wrong graded commit',
        )
        shared = exact_keys(record['scores'], self.criterion_keys('submission'), f'{submission_id} scores')
        individual_criteria = self.criterion_keys('individual')
        student_ids = (
            [member['student_id'] for member in self.roster[submission_id]['members']] if individual_criteria else []
        )
        individual = exact_keys(record['individual_scores'], student_ids, f'{submission_id} individual scores')
        for criterion_key, assessment in shared.items():
            self.validate_assessment(assessment, criterion_key, author, f'{submission_id} submission {criterion_key}')
        for student_id, value in individual.items():
            assessments = exact_keys(value, individual_criteria, f'{submission_id} {student_id} individual scores')
            for criterion_key, assessment in assessments.items():
                self.validate_assessment(
                    assessment, criterion_key, author, f'{submission_id} {student_id} {criterion_key}'
                )
        nonempty(record['summary'], f'{submission_id} summary')
        strings(record['flags'], f'{submission_id} flags')
        return cast(Evaluation, record)

    def validate_assessment(self, assessment: object, criterion_key: str, author: str, label: str) -> None:
        required = (*ASSESSMENT_FIELDS, 'rationale') if author == 'arbiter' else ASSESSMENT_FIELDS
        fields = exact_keys(assessment, required, label)
        criterion = self.criteria[criterion_key]
        score = fields['score']
        if 'allowed_scores' in criterion:
            require(
                type(score) in (int, float) and score in criterion['allowed_scores'],
                f'{label}: score is not on the allowed scale',
            )
        else:
            require(type(score) is int and 0 <= score <= criterion['max_score'], f'{label}: score out of range')
        nonempty(fields['feedback'], f'{label} feedback')
        strings(fields['evidence'], f'{label} evidence', 1)
        if author == 'arbiter':
            nonempty(fields['rationale'], f'{label} rationale')

    def schema(self, author: str) -> dict[str, object]:
        text = {'type': 'string', 'minLength': 1}
        criterion_schemas: dict[str, dict[str, object]] = {}
        for key, criterion in self.criteria.items():
            properties: dict[str, object] = {
                'score': {'type': 'integer', 'minimum': 0, 'maximum': criterion['max_score']},
                'feedback': text,
                'evidence': {'type': 'array', 'minItems': 1, 'items': text},
            }
            if 'allowed_scores' in criterion:
                properties['score'] = {'type': 'number', 'enum': criterion['allowed_scores']}
            if author == 'arbiter':
                properties['rationale'] = text
            criterion_schemas[key] = object_schema(properties)

        shared = object_schema({key: criterion_schemas[key] for key in self.criterion_keys('submission')})
        individual_keys = self.criterion_keys('individual')
        individual = object_schema({key: criterion_schemas[key] for key in individual_keys})
        fields: dict[str, object] = {
            'submission_id': {'enum': list(self.roster)},
            'author': {'enum': ['arbiter'] if author == 'arbiter' else self.config['graders']},
            'manifest_digest': text,
            'commit': text,
            'scores': shared,
            'individual_scores': {'type': 'object'},
            'summary': {
                **text,
                'description': 'Overall submission remark: strengths, gaps and priorities for follow-up.',
            },
            'flags': {'type': 'array', 'items': text},
        }
        member_constraints = []
        for submission_id, submission in self.roster.items():
            members = {member['student_id']: individual for member in submission['members']} if individual_keys else {}
            member_constraints.append(
                {
                    'if': {'properties': {'submission_id': {'const': submission_id}}},
                    'then': {'properties': {'individual_scores': object_schema(members)}},
                }
            )
        return {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            **object_schema(fields),
            'allOf': member_constraints,
        }

    def template(self, submission_id: str, author: str) -> DraftEvaluation:
        manifest = self.prepared()
        require(submission_id in self.roster, f'Unknown submission {submission_id}')
        require(author in self.config['graders'] + ['arbiter'], f'Unknown author {author}')

        def blank_assessments(scope: str) -> dict[str, DraftAssessment]:
            return {key: self.blank_assessment(author) for key in self.criterion_keys(scope)}

        members = self.roster[submission_id]['members'] if self.criterion_keys('individual') else []
        return {
            'submission_id': submission_id,
            'author': author,
            'manifest_digest': digest(manifest),
            'commit': manifest['submissions'][submission_id]['commit'],
            'scores': blank_assessments('submission'),
            'individual_scores': {member['student_id']: blank_assessments('individual') for member in members},
            'summary': '',
            'flags': [],
        }

    @staticmethod
    def blank_assessment(author: str) -> DraftAssessment:
        assessment: DraftAssessment = {'score': None, 'feedback': '', 'evidence': []}
        if author == 'arbiter':
            assessment['rationale'] = ''
        return assessment

    def state(self) -> ReviewState:
        manifest = self.prepared()
        authors = [*self.config['graders'], 'arbiter']
        self.validate_record_paths(authors)
        return {
            submission_id: self.submission_state(submission_id, submission, manifest, authors)
            for submission_id, submission in self.roster.items()
        }

    def validate_record_paths(self, authors: list[str]) -> None:
        for directory in (self.root / 'evaluations').iterdir():
            require(directory.name in authors and directory.is_dir(), f'Unexpected evaluations path: {directory}')
            for path in record_paths(directory):
                require(path.suffix == '.json' and path.stem in self.roster, f'Unexpected evaluation: {path}')
        for folder in ('verification', 'review'):
            for path in record_paths(self.root / folder):
                require(path.suffix == '.json' and path.stem in self.roster, f'Unknown record: {path}')
        for folder_path in (self.root / 'corrections').iterdir():
            require(
                folder_path.is_dir() and folder_path.name in self.roster, f'Unknown correction directory {folder_path}'
            )
            for path in record_paths(folder_path):
                require(path.suffix == '.json', f'Unexpected correction file {path}')

    def read_evaluations(
        self,
        submission_id: str,
        manifest: SourceManifest,
        authors: list[str],
    ) -> dict[str, Evaluation]:
        is_revision = (self.root / 'revision' / 'revision.json').is_file()
        evaluations: dict[str, Evaluation] = {}
        for author in authors:
            path = self.root / 'evaluations' / author / f'{submission_id}.json'
            if not path.exists():
                continue
            record = self.validate_record(read_json(path), submission_id, author, manifest)
            if is_revision:
                from grading_revisions import validate_carried

                validate_carried(self, record, author, submission_id)
            evaluations[author] = record
        return evaluations

    def apply_corrections(
        self,
        submission_id: str,
        manifest: SourceManifest,
        proposal: Evaluation | None,
    ) -> tuple[Evaluation | None, list[Correction]]:
        history: list[Correction] = []
        for path in sorted((self.root / 'corrections' / submission_id).glob('*.json')):
            require(proposal is not None, f'{submission_id}: correction without arbitration')
            correction = exact_keys(read_json(path), Correction.__annotations__, str(path))
            require(correction['parent_digest'] == digest(proposal), f'{path}: stale correction parent')
            nonempty(correction['reason'], f'{path} reason')
            strings(correction['evidence'], f'{path} evidence', 1)
            proposal = self.validate_record(correction['replacement'], submission_id, 'arbiter', manifest)
            history.append(cast(Correction, correction))
        return proposal, history

    def submission_state(
        self,
        submission_id: str,
        submission: Submission,
        manifest: SourceManifest,
        authors: list[str],
    ) -> ReviewItem:
        coverage: dict[str, Coverage] = {}
        missing_coverage: list[str] = []
        if (self.root / 'input' / 'verification.json').is_file():
            from grading_coverage import read_coverage

            coverage, missing_coverage = read_coverage(
                self, submission_id, manifest['submissions'][submission_id]['commit']
            )
        evaluations = self.read_evaluations(submission_id, manifest, authors)
        proposal, history = self.apply_corrections(submission_id, manifest, evaluations.get('arbiter'))
        proposal_dependencies: dict[str, object] = {
            'submission': submission,
            'evaluations': evaluations,
            'corrections': history,
        }
        if coverage:
            proposal_dependencies['coverage'] = coverage
        proposal_digest = digest(proposal_dependencies)
        verification_path = self.root / 'verification' / f'{submission_id}.json'
        verification = None
        verification_current = False
        resolved = False
        if verification_path.exists():
            require(proposal is not None, f'{submission_id}: verification without verdict')
            verification = parse_verification(verification_path, submission_id)
            verification_current = verification['proposal_digest'] == proposal_digest
            resolved = not verification['unresolved'] and all(
                check['status'] == 'confirmed' or not check.get('grade_affecting', True)
                for check in verification['checks']
            )
        bundle_digest = digest({'proposal_digest': proposal_digest, 'verification': verification})
        approval_path = self.root / 'review' / f'{submission_id}.json'
        approval = parse_approval(approval_path, submission_id) if approval_path.exists() else None
        missing = [grader for grader in self.config['graders'] if grader not in evaluations]
        ready = not missing and not missing_coverage and proposal is not None and verification_current and resolved
        return {
            'submission': submission,
            'evaluations': evaluations,
            'proposal': proposal,
            'history': history,
            'proposal_digest': proposal_digest,
            'verification': verification,
            'verification_current': verification_current,
            'bundle_digest': bundle_digest,
            'missing': missing,
            'coverage': coverage,
            'missing_coverage': missing_coverage,
            'ready': ready,
            'approved': ready and approval is not None and approval['bundle_digest'] == bundle_digest,
        }
