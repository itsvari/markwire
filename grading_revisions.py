"""Plan scoped grading changes against an immutable, prepared workspace."""

from __future__ import annotations

import csv
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TypedDict, cast

from grading_bundles import is_linked_path, path_name
from grading_core import (
    DEFAULT_ROUNDING_DIGITS,
    GIT_COMMIT_PATTERN,
    Criterion,
    DraftAssessment,
    DraftEvaluation,
    Evaluation,
    Member,
    ReviewItem,
    ReviewState,
    Workspace,
    digest,
    exact_keys,
    file_digest,
    identifier,
    json_object,
    nonempty,
    read_json,
    require,
    write_json,
    write_text,
)
from grading_coverage import coverage_record_path
from grading_setup import (
    PREFERENCES_PATH,
    VERSION,
    VerificationCheck,
    load_verification,
    validate_verification,
)
from grading_sources import inspect_snapshots, local_inventory, prepare

REVISION_VERSION = 1
CRITERION_PRESENTATION_FIELDS = frozenset({'label', 'weight'})
TARGETED_INPUTS = frozenset({'rubric.md', 'verification.json'})
TARGETED_CONFIGURATION_FIELDS = frozenset({'title', 'criteria', 'review_thresholds', 'rounding_digits', 'graders'})


class AssessmentTarget(TypedDict):
    criterion: str
    member: str | None


class CarriedAssessment(AssessmentTarget):
    assessment: DraftAssessment


class CarryRecord(TypedDict):
    assessments: list[CarriedAssessment]
    summary: str
    flags: list[str]
    origin: str


class SubmissionPlan(TypedDict):
    reassess: list[AssessmentTarget]
    carry: list[AssessmentTarget]
    source_changed: bool
    recompute: bool
    review: bool


class RevisionDraft(TypedDict):
    schema_version: int
    reason: str
    parent_manifest_digest: str
    assignment_digest: str
    input_replacements: dict[str, str]
    replacement_commits: dict[str, str]
    replacement_sources: dict[str, str]
    added_criteria: list[str]
    changed_criteria: list[str]
    retired_criteria: list[str]
    retained_checks: list[str]
    submissions: dict[str, SubmissionPlan]
    carry_digests: dict[str, str]
    prior_bundle_digests: dict[str, str]
    prior_approvals: dict[str, str]


class RevisionMetadata(RevisionDraft):
    plan_digest: str


@dataclass(frozen=True)
class RevisionChanges:
    document: Mapping[str, object]
    inputs: Mapping[Path, Path]
    commits: Mapping[str, str]
    sources: Mapping[str, Path]


@dataclass(frozen=True)
class RevisionPlan:
    payload: RevisionDraft
    assignment: Mapping[str, object]
    replacements: Mapping[Path, Path]
    replacement_commits: Mapping[str, str]
    replacement_sources: Mapping[str, Path]
    submissions: Mapping[str, SubmissionPlan]
    carry: Mapping[tuple[str, str], CarryRecord]
    checks: Sequence[VerificationCheck]
    retained_checks: frozenset[str]


def mapping(value: object, name: str) -> dict[str, object]:
    return json_object(value, name)


def text_values(value: object, name: str) -> list[str]:
    require(
        isinstance(value, list) and all(isinstance(item, str) for item in value), f'{name} must be a list of strings'
    )
    return cast(list[str], value)


def resolve_payload(directory: Path, location: object, label: str) -> Path:
    return (directory / Path(nonempty(location, label)).expanduser()).resolve(strict=True)


def content_digest(source: Path) -> str:
    return file_digest(source) if source.is_file() else digest(local_inventory(source))


def read_changes(changes_path: Path) -> RevisionChanges:
    changes = mapping(read_json(changes_path), 'Revision changes')
    supported = {'reason', 'assignment', 'inputs', 'changed_criteria', 'resubmissions', 'regrades'}
    require(set(changes) <= supported, f'Unknown revision keys: {", ".join(sorted(set(changes) - supported))}')
    nonempty(changes.get('reason'), 'Revision reason')
    replacements: dict[Path, Path] = {}
    for name, location in mapping(changes.get('inputs', {}), 'Replacement inputs').items():
        relative = PurePosixPath(path_name(name))
        require(
            '.git' not in {part.casefold() for part in relative.parts} and relative.name.casefold() != 'roster.csv',
            f'Invalid revision input path: {name}',
        )
        replacement = resolve_payload(changes_path.parent, location, name)
        require(replacement.is_file(), f'{name}: replacement input must be a file')
        target = Path(*relative.parts)
        require(target not in replacements, f'Duplicate revision input path: {name}')
        replacements[target] = replacement
    replacement_commits: dict[str, str] = {}
    replacement_sources: dict[str, Path] = {}
    for submission_id, value in mapping(changes.get('resubmissions', {}), 'Resubmissions').items():
        if isinstance(value, str):
            require(GIT_COMMIT_PATTERN.fullmatch(value), f'{submission_id}: resubmission requires a full Git SHA')
            replacement_commits[submission_id] = value.lower()
        else:
            exact_keys(value, ['source'], f'{submission_id} resubmission')
            source = resolve_payload(
                changes_path.parent, mapping(value, 'Resubmission')['source'], 'Resubmitted source'
            )
            require(
                source.is_dir() or source.suffix.lower() == '.zip',
                f'{submission_id}: expected a local directory or ZIP archive',
            )
            replacement_sources[submission_id] = source
    return RevisionChanges(changes, replacements, replacement_commits, replacement_sources)


def targets_for(criteria: Mapping[str, Mapping[str, object]], members: Sequence[Member]) -> list[AssessmentTarget]:
    return [
        {'criterion': criterion, 'member': member}
        for criterion, definition in criteria.items()
        for member in (
            [None] if definition['scope'] == 'submission' else [student['student_id'] for student in members]
        )
    ]


def read_regrades(value: object, targets: Mapping[str, list[AssessmentTarget]]) -> dict[str, list[AssessmentTarget]]:
    require(isinstance(value, list), 'Regrades must be a list')
    result: dict[str, list[AssessmentTarget]] = {}
    for request in cast(list[object], value):
        selection = mapping(request, 'Regrade')
        require(set(selection) <= {'submission_id', 'criteria', 'member'}, 'Unexpected regrade keys')
        submission_id = identifier(selection.get('submission_id'), 'Regrade submission')
        require(submission_id in targets, f'Unknown regrade submission: {submission_id}')
        member = selection.get('member')
        criteria = text_values(selection.get('criteria', []), 'Regrade criteria')
        matching = [
            target
            for target in targets[submission_id]
            if (member is None or member == target['member']) and (not criteria or target['criterion'] in criteria)
        ]
        require(
            matching and set(criteria) <= {target['criterion'] for target in matching},
            f'{submission_id}: regrade selection has invalid criteria or member',
        )
        result.setdefault(submission_id, []).extend(matching)
    return result


def carry_records(
    parent: Workspace,
    state: ReviewState,
    submissions: Mapping[str, SubmissionPlan],
    authors: Sequence[str],
    retired: set[str],
    grader_cohort_changed: bool,
) -> dict[tuple[str, str], CarryRecord]:
    carry: dict[tuple[str, str], CarryRecord] = {}
    for submission_id, scope in submissions.items():
        if not scope['carry']:
            continue
        for author in authors:
            if author == 'arbiter' and grader_cohort_changed:
                continue
            record = previous_assessment(parent, state[submission_id], author, submission_id)
            if record is None:
                continue
            assessments = available_assessments(record, scope['carry'])
            if not assessments:
                continue
            refresh_summary = (
                bool(scope['reassess'])
                or len(assessments) < len(scope['carry'])
                or (bool(retired) and author == 'arbiter')
            )
            carry[(author, submission_id)] = {
                'assessments': assessments,
                'summary': '' if refresh_summary else record['summary'],
                'flags': [] if refresh_summary else record['flags'],
                'origin': digest(record),
            }
    return carry


def previous_assessment(
    workspace: Workspace,
    state: ReviewItem,
    author: str,
    submission_id: str,
) -> DraftEvaluation | Evaluation | None:
    original = state['proposal'] if author == 'arbiter' else state['evaluations'].get(author)
    if original is not None:
        return original
    if author not in [*workspace.config['graders'], 'arbiter']:
        return None
    return carried_template(workspace, author, submission_id, workspace.template(submission_id, author))


def available_assessments(
    record: DraftEvaluation | Evaluation,
    targets: Sequence[AssessmentTarget],
) -> list[CarriedAssessment]:
    assessments = (CarriedAssessment(**target, assessment=draft_assessment(record, target)) for target in targets)
    return [item for item in assessments if item['assessment']['score'] is not None]


def plan_verification(
    parent: Workspace,
    replacement: Path | None,
    criteria: set[str],
) -> tuple[list[VerificationCheck], set[str], set[str]]:
    original = load_verification(parent.root)
    revised = original
    if replacement is not None:
        checklist = mapping(read_json(replacement), 'Verification checklist')
        exact_keys(checklist, ('version', 'checks'), 'Verification checklist')
        require(
            type(checklist['version']) is int and checklist['version'] == VERSION,
            'Unsupported verification checklist version',
        )
        revised = cast(list[VerificationCheck], checklist['checks'])
    validate_verification(revised, sorted(criteria))
    before = {check['id']: check for check in original}
    after = {check['id']: check for check in revised}
    retained = {
        name
        for name in before.keys() & after.keys()
        if all(before[name][field] == after[name][field] for field in ('instructions', 'capabilities'))
    }
    affected: set[str] = set()
    for check_identifier in before.keys() | after.keys():
        original_check, revised_check = before.get(check_identifier), after.get(check_identifier)
        if original_check == revised_check:
            continue
        for check in (original_check, revised_check):
            if check is not None:
                affected.update(check['criteria'] or criteria)
    return revised, retained, affected & criteria


def build_plan(parent: Workspace, changes_path: Path) -> RevisionPlan:
    manifest = parent.prepared()
    requested = read_changes(changes_path)
    changes = requested.document
    replacement_hashes = {relative.as_posix(): file_digest(source) for relative, source in requested.inputs.items()}
    replacement_source_digests = {
        submission_id: content_digest(path) for submission_id, path in requested.sources.items()
    }
    assignment = dict(parent.config)
    if 'assignment' in changes:
        assignment = mapping(
            read_json(resolve_payload(changes_path.parent, changes['assignment'], 'Replacement assignment')),
            'Replacement assignment',
        )
    require(
        assignment.get('assignment_id') == parent.config['assignment_id'], 'Assignment ID cannot change in a revision'
    )
    require(assignment.get('policy_confirmed') is True, 'Confirm the revised assignment policy')
    require(
        assignment.get('snapshot_policy') == parent.config['snapshot_policy'],
        'Keep the original snapshot policy and pin resubmissions explicitly',
    )
    authors = text_values(assignment.get('graders'), 'Graders')
    require(
        requested.commits.keys() | requested.sources.keys() <= parent.roster.keys(),
        'Resubmissions must match existing submission IDs',
    )
    for submission_id, commit in requested.commits.items():
        require(bool(parent.roster[submission_id]['branch']), f'{submission_id}: local submissions use a source path')
        require(
            commit != manifest['submissions'][submission_id]['commit'],
            f'{submission_id}: use a regrade for unchanged source',
        )
    for submission_id in requested.sources:
        require(not parent.roster[submission_id]['branch'], f'{submission_id}: Git submissions use a full commit SHA')
    old_criteria: Mapping[str, Criterion] = parent.criteria
    proposed = assignment.get('criteria')
    require(isinstance(proposed, list) and bool(proposed), 'Revision must retain at least one criterion')
    new_criteria = {
        identifier(item.get('key'), 'Criterion key'): item
        for item in (mapping(value, 'Criterion') for value in cast(list[object], proposed))
    }
    require(len(new_criteria) == len(cast(list[object], proposed)), 'Duplicate criterion keys')
    require(
        all(value.get('scope') in ('submission', 'individual') for value in new_criteria.values()),
        'Confirm all criterion scopes before planning',
    )
    retired = set(old_criteria) - set(new_criteria)
    declared = set(text_values(changes.get('changed_criteria', []), 'Changed rubric criteria'))
    require(declared <= old_criteria.keys() | new_criteria.keys(), 'Unknown changed rubric criterion')
    altered_inputs = {
        key
        for key, value in replacement_hashes.items()
        if not (parent.root / 'input' / key).is_file() or value != file_digest(parent.root / 'input' / key)
    }
    if 'rubric.md' in altered_inputs:
        require(
            'changed_criteria' in changes,
            'List changed_criteria for a rubric document edit (use [] for presentation only)',
        )
    changed = (declared & new_criteria.keys()) | (set(new_criteria) - set(old_criteria))
    for criterion in set(new_criteria) & set(old_criteria):
        old_definition = {
            key: value for key, value in old_criteria[criterion].items() if key not in CRITERION_PRESENTATION_FIELDS
        }
        new_definition = {
            key: value for key, value in new_criteria[criterion].items() if key not in CRITERION_PRESENTATION_FIELDS
        }
        if old_definition != new_definition:
            changed.add(criterion)
    checks, retained_checks, affected_by_verification = plan_verification(
        parent,
        requested.inputs.get(Path('verification.json')),
        set(new_criteria),
    )
    changed.update(affected_by_verification)
    weights_changed = any(
        value.get('weight') != (old_criteria[key]['weight'] if key in old_criteria else None)
        for key, value in new_criteria.items()
    )
    rounding_changed = assignment.get('rounding_digits', DEFAULT_ROUNDING_DIGITS) != parent.config.get(
        'rounding_digits', DEFAULT_ROUNDING_DIGITS
    )
    other_policy_changed = any(
        assignment.get(key) != parent.config.get(key)
        for key in (assignment.keys() | parent.config.keys()) - TARGETED_CONFIGURATION_FIELDS
    )
    scope_all = bool(altered_inputs - TARGETED_INPUTS) or other_policy_changed
    aggregate_change = bool(changed or retired or weights_changed or rounding_changed)
    grading_roles_changed = authors != parent.config['graders']
    all_targets = {
        submission_id: targets_for(new_criteria, submission['members'])
        for submission_id, submission in parent.roster.items()
    }
    regrades = read_regrades(changes.get('regrades', []), all_targets)
    submissions: dict[str, SubmissionPlan] = {}
    for submission_id, targets in all_targets.items():
        source_changed = submission_id in requested.commits or submission_id in requested.sources
        targeted = regrades.get(submission_id, [])
        reassess = [
            target
            for target in targets
            if source_changed or scope_all or target['criterion'] in changed or target in targeted
        ]
        submissions[submission_id] = {
            'reassess': reassess,
            'carry': [target for target in targets if target not in reassess],
            'source_changed': source_changed,
            'recompute': aggregate_change or bool(reassess),
            'review': aggregate_change or grading_roles_changed or bool(reassess),
        }
    state = parent.state()
    carry = carry_records(parent, state, submissions, [*authors, 'arbiter'], retired, grading_roles_changed)
    metadata: RevisionDraft = {
        'schema_version': REVISION_VERSION,
        'reason': cast(str, changes['reason']),
        'parent_manifest_digest': digest(manifest),
        'assignment_digest': digest(assignment),
        'input_replacements': replacement_hashes,
        'replacement_commits': dict(requested.commits),
        'replacement_sources': replacement_source_digests,
        'added_criteria': sorted(set(new_criteria) - set(old_criteria)),
        'changed_criteria': sorted(changed),
        'retired_criteria': sorted(retired),
        'retained_checks': sorted(retained_checks) if not scope_all else [],
        'submissions': submissions,
        'carry_digests': {
            f'{author}/{submission_id}': digest(record) for (author, submission_id), record in carry.items()
        },
        'prior_bundle_digests': {submission_id: item['bundle_digest'] for submission_id, item in state.items()},
        'prior_approvals': {
            submission_id: file_digest(parent.root / 'review' / f'{submission_id}.json')
            for submission_id, item in state.items()
            if item['approved']
        },
    }
    return RevisionPlan(
        metadata,
        assignment,
        requested.inputs,
        requested.commits,
        requested.sources,
        submissions,
        carry,
        checks,
        frozenset() if scope_all else frozenset(retained_checks),
    )


def plan_revision(parent: Workspace, changes_path: Path) -> RevisionMetadata:
    inspect_snapshots(parent, parent.prepared())
    plan = build_plan(parent, changes_path)
    return {**plan.payload, 'plan_digest': digest(plan.payload)}


def revision_metadata(workspace: Workspace) -> RevisionMetadata | None:
    path = workspace.root / 'revision/revision.json'
    if not path.exists():
        return None
    metadata = mapping(read_json(path), 'Revision metadata')
    exact_keys(metadata, RevisionMetadata.__annotations__, 'Revision metadata')
    require(metadata['schema_version'] == REVISION_VERSION, 'Unsupported revision metadata version')
    return cast(RevisionMetadata, metadata)


def load_carried(workspace: Workspace, author: str, submission_id: str) -> CarryRecord | None:
    metadata = revision_metadata(workspace)
    if metadata is None:
        return None
    expected = metadata['carry_digests'].get(f'{author}/{submission_id}')
    if expected is None:
        return None
    path = workspace.root / 'revision/carried' / author / f'{submission_id}.json'
    record = read_json(path)
    require(digest(record) == expected, f'{submission_id}/{author}: carried assessment checksum changed')
    return cast(CarryRecord, record)


def assessment_group(record: Mapping[str, object], member: str | None) -> dict[str, object]:
    if member is None:
        return mapping(record['scores'], 'Submission criteria')
    return mapping(mapping(record['individual_scores'], 'Individual criteria')[member], 'Member criteria')


def assessment_value(record: Mapping[str, object], target: AssessmentTarget) -> object:
    return assessment_group(record, target['member'])[target['criterion']]


def draft_assessment(record: DraftEvaluation | Evaluation, target: AssessmentTarget) -> DraftAssessment:
    member = target['member']
    assessments = record['scores'] if member is None else record['individual_scores'][member]
    return DraftAssessment(**assessments[target['criterion']])


def carried_template(
    workspace: Workspace,
    author: str,
    submission_id: str,
    blank: DraftEvaluation,
) -> DraftEvaluation:
    carried = load_carried(workspace, author, submission_id)
    if carried is None:
        return blank
    shared_scores = dict(blank['scores'])
    individual_scores = {member: dict(assessments) for member, assessments in blank['individual_scores'].items()}
    for item in carried['assessments']:
        member = item['member']
        assessments = shared_scores if member is None else individual_scores[member]
        assessments[item['criterion']] = item['assessment']
    return {
        **blank,
        'scores': shared_scores,
        'individual_scores': individual_scores,
        'summary': carried['summary'],
        'flags': carried['flags'],
    }


def pending_assessments(record: DraftEvaluation) -> list[AssessmentTarget]:
    groups = [(None, record['scores']), *record['individual_scores'].items()]
    return [
        {'criterion': criterion, 'member': member}
        for member, assessments in groups
        for criterion, assessment in assessments.items()
        if assessment['score'] is None
    ]


def validate_carried(workspace: Workspace, record: Mapping[str, object], author: str, submission_id: str) -> None:
    carried = load_carried(workspace, author, submission_id)
    if carried is None:
        return
    for item in carried['assessments']:
        require(
            assessment_value(record, item) == item['assessment'],
            f'{submission_id}/{author}: carried assessment changed for {item["criterion"]}. Open a scoped regrade first.',
        )


def copy_grading_evidence(parent: Workspace, workspace: Workspace, author: str, location: str) -> None:
    source = parent.root / 'evidence' / author / location
    if not source.is_dir():
        return
    target = workspace.root / 'evidence' / author / location
    require(
        not is_linked_path(source) and all(not is_linked_path(path) for path in source.rglob('*')),
        f'{location}/{author}: inherited evidence contains a symbolic link',
    )
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.DS_Store', '._*'))


def copy_preferences(parent: Workspace, root: Path, plan: RevisionPlan) -> None:
    source = parent.root / PREFERENCES_PATH
    if not source.is_file():
        return
    original = mapping(read_json(source), 'Private preferences')
    removed = set(parent.config['graders']) - set(text_values(plan.assignment['graders'], 'Graders'))
    role_preferences = {
        key: {role: configuration for role, configuration in mapping(original[key], key).items() if role not in removed}
        for key in ('models', 'roles')
    }
    revised = {**original, **role_preferences}
    write_json(root / PREFERENCES_PATH, revised)


def copy_coverage(parent: Workspace, workspace: Workspace, plan: RevisionPlan) -> None:
    if not plan.retained_checks:
        return
    state = parent.state()
    checklist_digest = digest(plan.checks)
    for submission_id, scope in plan.submissions.items():
        if scope['source_changed']:
            continue
        for author in workspace.config['graders']:
            original = state[submission_id]['coverage'].get(author)
            if original is None:
                continue
            observations = [item for item in original['observations'] if item['check_id'] in plan.retained_checks]
            target = coverage_record_path(workspace.root, author, submission_id)
            write_json(target, {**original, 'checklist_digest': checklist_digest, 'observations': observations})
            copy_grading_evidence(parent, workspace, author, submission_id)


def revised_roster_row(row: dict[str, str], parent: Workspace, plan: RevisionPlan) -> dict[str, str]:
    submission_id = row['submission_id'].strip()
    replacement = plan.replacement_sources.get(submission_id)
    source = row['repo_url'].strip()
    if replacement is not None:
        source = str(replacement)
    elif ':' not in source and not Path(source).is_absolute():
        source = str((parent.root / source).resolve())
    commit = row['commit']
    if parent.config['snapshot_policy']['mode'] == 'explicit':
        commit = plan.replacement_commits.get(submission_id, commit)
    return {**row, 'repo_url': source, 'commit': commit}


def revise_roster(root: Path, parent: Workspace, plan: RevisionPlan) -> None:
    target = root / 'input/roster.csv'
    with target.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        revised_rows = [revised_roster_row(row, parent, plan) for row in reader]
    require(fields, 'Roster headers are missing')
    with target.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=cast(list[str], fields))
        writer.writeheader()
        writer.writerows(revised_rows)


def stage_revision(
    parent: Workspace,
    root: Path,
    plan: RevisionPlan,
    plan_digest: str,
    engine_files: tuple[str, ...],
    engine_root: Path,
) -> Workspace:
    root.mkdir()
    for name in ('AGENTS.md', 'GRADING_MANUAL.md', 'SETUP.md', '.gitignore'):
        if (parent.root / name).is_file():
            shutil.copy2(parent.root / name, root / name)
    for name in engine_files:
        shutil.copy2(engine_root / name, root / name)
    for document in ('docs/t3-orchestration.md', 'docs/ongoing-grading.md'):
        target = root / document
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(engine_root / document, target)
    setup = root / 'SETUP.md'
    if setup.is_file():
        original = setup.read_text(encoding='utf-8')
        if 'docs/ongoing-grading.md' not in original:
            write_text(
                setup,
                original.rstrip()
                + '\n\nSee the [revision, remote-check, and decision guide](docs/ongoing-grading.md) for revisions, '
                'stage decisions and remote checks.\n',
            )
    shutil.copytree(
        engine_root / 'report_assets', root / 'report_assets', ignore=shutil.ignore_patterns('.DS_Store', '._*')
    )
    shutil.copytree(parent.root / 'input', root / 'input', ignore=shutil.ignore_patterns('.DS_Store', '._*'))
    write_json(root / 'assignment.json', plan.assignment)
    for relative, source in plan.replacements.items():
        target = root / 'input' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        require(
            file_digest(target) == plan.payload['input_replacements'][relative.as_posix()],
            f'Revision input changed while copying: {relative}',
        )
    revise_roster(root, parent, plan)
    copy_preferences(parent, root, plan)
    for (author, submission_id), carry in plan.carry.items():
        write_json(root / 'revision/carried' / author / f'{submission_id}.json', carry)
    write_json(root / 'revision/revision.json', {**plan.payload, 'plan_digest': plan_digest})
    workspace = Workspace(root)
    parent_manifest = parent.prepared()
    inherited = {
        submission_id: (parent.root / 'snapshots' / submission_id, metadata)
        for submission_id, metadata in parent_manifest['submissions'].items()
        if not plan.submissions[submission_id]['source_changed']
    }
    prepare(workspace, inherited_snapshots=inherited, replacement_commits=plan.replacement_commits)
    copy_coverage(parent, workspace, plan)
    checksums = plan.payload['replacement_sources']
    for submission_id, source in plan.replacement_sources.items():
        require(
            content_digest(source) == checksums[submission_id],
            f'{submission_id}: resubmission changed during preparation',
        )
    return workspace


def promote_carried(parent: Workspace, workspace: Workspace, plan: RevisionPlan) -> None:
    manifest = workspace.prepared()
    for author in sorted({author for author, _ in plan.carry}):
        copy_grading_evidence(parent, workspace, author, '_shared')
    for (author, submission_id), carried in plan.carry.items():
        record = carried_template(workspace, author, submission_id, workspace.template(submission_id, author))
        copy_grading_evidence(parent, workspace, author, submission_id)
        if carried['summary'] and not pending_assessments(record):
            workspace.validate_record(record, submission_id, author, manifest)
            write_json(workspace.root / 'evaluations' / author / f'{submission_id}.json', record)


def reviewed_content(state: ReviewItem) -> dict[str, object]:
    proposal = state['proposal']
    return {
        'evaluations': {
            author: review_dependencies(record, 'manifest_digest')
            for author, record in state['evaluations'].items()
            if author != 'arbiter'
        },
        'proposal': review_dependencies(proposal, 'manifest_digest') if proposal else None,
        'coverage': {
            author: review_dependencies(record, 'checklist_digest') for author, record in state['coverage'].items()
        },
    }


def review_dependencies(record: Mapping[str, object], fingerprint_field: str) -> dict[str, object]:
    return {key: value for key, value in record.items() if key != fingerprint_field}


def carry_review(parent: Workspace, workspace: Workspace, plan: RevisionPlan, destination: Path) -> None:
    old_state = parent.state()
    revised_state = workspace.state()
    inherited_approvals: dict[str, object] = {}
    carried_verifications: list[str] = []
    for submission_id, scope in plan.submissions.items():
        prior = old_state[submission_id]
        current = revised_state[submission_id]
        prior_verification = prior['verification']
        if (
            scope['review']
            or not prior['verification_current']
            or prior_verification is None
            or current['missing']
            or not current['proposal']
        ):
            continue
        require(
            reviewed_content(prior) == reviewed_content(current),
            f'{submission_id}: review dependencies changed. Open a targeted revision for this submission.',
        )
        verification = {**prior_verification, 'proposal_digest': current['proposal_digest']}
        copy_grading_evidence(parent, workspace, 'sanity', submission_id)
        write_json(workspace.root / 'verification' / f'{submission_id}.json', verification)
        carried_verifications.append(submission_id)
        if prior['approved']:
            inherited_approvals[submission_id] = read_json(parent.root / 'review' / f'{submission_id}.json')
    if carried_verifications:
        copy_grading_evidence(parent, workspace, 'sanity', '_shared')
    revised_state = workspace.state()
    for submission_id, approval in inherited_approvals.items():
        require(
            revised_state[submission_id]['ready'], f'{submission_id}: carried review no longer satisfies release checks'
        )
        inherited = {
            **mapping(approval, 'Original approval'),
            'bundle_digest': revised_state[submission_id]['bundle_digest'],
        }
        write_json(workspace.root / 'review' / f'{submission_id}.json', inherited)
    write_json(
        workspace.root / 'revision/origins.json',
        {
            'parent_workspace': str(parent.root.relative_to(destination.parent))
            if parent.root.parent == destination.parent
            else str(parent.root),
            'carried_verifications': carried_verifications,
            'carried_approvals': inherited_approvals,
        },
    )
    workspace.state()


def create_revision(
    parent: Workspace,
    destination: Path,
    changes_path: Path,
    confirmed_plan_digest: str,
    engine_files: tuple[str, ...],
    engine_root: Path,
) -> RevisionMetadata:
    destination = destination.expanduser().resolve()
    require(
        destination != parent.root and parent.root not in destination.parents, 'Choose a separate revision directory'
    )
    require(not destination.exists(), f'Revision destination already exists: {destination}')
    snapshot = parent.prepared()
    inspect_snapshots(parent, snapshot)
    plan = build_plan(parent, changes_path)
    plan_digest = digest(plan.payload)
    require(plan_digest == confirmed_plan_digest, 'Revision plan changed. Review the new plan before applying.')
    with TemporaryDirectory(dir=destination.parent, prefix='.grading-revision-') as directory:
        temporary = Path(directory) / 'workspace'
        workspace = stage_revision(parent, temporary, plan, plan_digest, engine_files, engine_root)
        promote_carried(parent, workspace, plan)
        carry_review(parent, workspace, plan, destination)
        require(
            digest(build_plan(parent, changes_path).payload) == plan_digest,
            'Parent inputs or records changed while creating the revision. Review the new plan.',
        )
        require(not destination.exists(), f'Revision destination already exists: {destination}')
        temporary.rename(destination)
    return {**plan.payload, 'plan_digest': plan_digest}
