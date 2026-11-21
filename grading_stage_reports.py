"""Stage guides, evidence-linked findings and reviewed decision receipts."""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal, TypedDict, cast

from grading_bundles import Result, Task, imported_result, read_task
from grading_core import (
    GradingError,
    ReviewItem,
    ReviewState,
    Workspace,
    digest,
    exact_keys,
    identifier,
    json_object,
    nonempty,
    read_json,
    require,
    strings,
    write_json,
    write_text,
)
from grading_revisions import SubmissionPlan
from grading_setup import VerificationCheck, load_verification

Stage = Literal['setup', 'grading', 'arbitration', 'sanity', 'release']
Severity = Literal['blocker', 'grade_impact', 'advisory']
FindingStatus = Literal['open', 'reconciled']
STAGES: tuple[Stage, ...] = ('setup', 'grading', 'arbitration', 'sanity', 'release')
SEVERITY_ORDER = {'blocker': 0, 'grade_impact': 1, 'advisory': 2}
STAGE_LABELS = {
    'setup': 'Setup',
    'grading': 'Independent grading',
    'arbitration': 'Arbitration',
    'sanity': 'Sanity review',
    'release': 'Release review',
}
WEIGHTED_REVIEW_THRESHOLD_PERCENTAGE_POINTS = 1
PERCENTAGE_SCALE = 100
REPORT_SCHEMA_VERSION = 1


FINDING_IDENTIFIER_HEX_LENGTH = 16
MINIMUM_SPREAD_SCORES = 2
FONT_FACES = (('Regular', 400), ('SemiBold', 600))
SCRIPT_ESCAPES = str.maketrans({'<': r'\u003c', '>': r'\u003e', '&': r'\u0026'})


def script_safe_json(payload: object) -> str:
    # prevent report data from closing the script element
    return json.dumps(payload, ensure_ascii=True).translate(SCRIPT_ESCAPES)


def report_stylesheet(assets: Path, stylesheet_name: str) -> str:
    stylesheet = (assets / stylesheet_name).read_text(encoding='utf-8')
    fonts = assets / 'fonts'
    if not fonts.is_dir():
        return stylesheet
    license_text = (fonts / 'LICENSE.txt').read_text(encoding='utf-8')
    font_faces = []
    for style, weight in FONT_FACES:
        font = base64.b64encode((fonts / f'IBMPlexMono-{style}.woff2').read_bytes()).decode('ascii')
        font_faces.append(
            f'@font-face {{ font-family: "IBM Plex Mono"; font-style: normal; font-weight: {weight}; '
            f'font-display: swap; src: url(data:font/woff2;base64,{font}) format("woff2"); }}'
        )
    return '\n'.join((f'/*\n{license_text}\n*/', *font_faces, stylesheet))


@dataclass(frozen=True)
class Source:
    author: str
    reference: str
    observation: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    consequence: str
    owner: str


@dataclass(frozen=True)
class Finding:
    id: str
    submission: str
    topic: str
    title: str
    summary: str
    severity: Severity
    sources: tuple[Source, ...]
    next_step: str
    options: tuple[Option, ...] = ()
    question: str = 'What should happen next?'
    status: FindingStatus = 'open'
    criterion: str | None = None
    member: str | None = None
    scenario: str | None = None
    impact_percentage_points: float | None = None


@dataclass(frozen=True)
class RemoteAttempt:
    task: Task
    result_path: Path
    result: Result | None


class GuideStop(TypedDict):
    title: str
    summary: str
    next_step: str


class Decision(TypedDict):
    issueId: str
    optionId: str
    note: str


class Progress(TypedDict):
    complete: int
    total: int
    label: str


class ReportDraft(TypedDict):
    schema_version: int
    run_id: str
    stage: Stage
    source_digest: str
    progress: Progress
    stops: list[GuideStop]
    findings: list[dict[str, object]]


class SavedOption(TypedDict):
    id: str
    label: str
    consequence: str
    owner: str


class SavedFinding(TypedDict):
    id: str
    status: str
    options: list[SavedOption]
    submission: str
    member: str | None
    criterion: str | None
    title: str
    sources: list[dict[str, object]]


def stage_name(value: str) -> Stage:
    for stage in STAGES:
        if stage == value:
            return stage
    raise GradingError(f'Unknown report stage: {value}')


def finding_id(*identity: str | None) -> str:
    return digest(identity)[:FINDING_IDENTIFIER_HEX_LENGTH]


def default_options(action: str, owner: str) -> tuple[Option, ...]:
    return (
        Option('request-review', action, 'Route this finding and its supporting evidence to the assigned role.', owner),
        Option(
            'defer',
            'Defer',
            'Leave this finding open for later attention. Counts as a completed choice for hand-off.',
            'ta',
        ),
        Option(
            'custom',
            'Specify an action',
            'Write instructions below for the assigned role. Instructions are required.',
            owner,
        ),
    )


def workflow_finding(
    submission: str,
    code: str,
    topic: str,
    title: str,
    summary: str,
    owner: str,
    next_step: str,
    sources: tuple[Source, ...] = (),
) -> Finding:
    return Finding(
        id=finding_id(submission, code),
        submission=submission,
        topic=topic,
        title=title,
        summary=summary,
        severity='blocker',
        sources=sources,
        next_step=next_step,
        options=default_options(next_step, owner),
    )


def record_flags(workspace: Workspace, submission: str, item: ReviewItem, include_verdict: bool) -> list[Finding]:
    sources_by_flag: defaultdict[str, list[Source]] = defaultdict(list)
    for author in workspace.config['graders']:
        evaluation = item['evaluations'].get(author)
        if evaluation is None:
            continue
        for index, flag in enumerate(evaluation['flags']):
            reference = f'evaluations/{author}/{submission}.json#/flags/{index}'
            sources_by_flag[' '.join(flag.split())].append(Source(author, reference, flag))
    verdict = item['proposal']
    if include_verdict and verdict is not None:
        reference = f'evaluations/arbiter/{submission}.json#/flags'
        if item['history']:
            correction = sorted((workspace.root / 'corrections' / submission).glob('*.json'))[-1]
            reference = correction.relative_to(workspace.root).as_posix() + '#/replacement/flags'
        for index, flag in enumerate(verdict['flags']):
            sources_by_flag[' '.join(flag.split())].append(Source('arbiter', f'{reference}/{index}', flag))
    return [
        Finding(
            finding_id(submission, 'flag', text),
            submission,
            'Evidence flags',
            text,
            text,
            'advisory',
            tuple(sources),
            'Review the source records and establish whether this affects a rubric decision.',
            default_options('Ask the arbiter to investigate', 'arbiter'),
        )
        for text, sources in sources_by_flag.items()
    ]


def assessment_sources(
    workspace: Workspace, submission: str, item: ReviewItem, criterion: str, member: str | None
) -> list[tuple[int | float, Source]]:
    score_path = f'individual_scores/{member}/{criterion}' if member else f'scores/{criterion}'
    assessments: list[tuple[int | float, Source]] = []
    for author in workspace.config['graders']:
        evaluation = item['evaluations'].get(author)
        if evaluation is None:
            continue
        scores = evaluation['individual_scores'][member] if member else evaluation['scores']
        score = scores[criterion]
        reference = f'evaluations/{author}/{submission}.json#/{score_path}'
        assessments.append(
            (score['score'], Source(author, reference, f'{score["score"]:g} marks', tuple(score['evidence'])))
        )
    return assessments


def score_findings(workspace: Workspace, submission: str, item: ReviewItem, include_verdict: bool) -> list[Finding]:
    thresholds = workspace.config['review_thresholds']
    weighted_threshold = thresholds.get(
        'weighted_percentage_points',
        WEIGHTED_REVIEW_THRESHOLD_PERCENTAGE_POINTS,
    )
    total_weight = sum(criterion['weight'] for criterion in workspace.criteria.values())
    findings: list[Finding] = []
    for criterion_id, criterion in workspace.criteria.items():
        members = (
            [member['student_id'] for member in item['submission']['members']]
            if criterion['scope'] == 'individual'
            else [None]
        )
        for member in members:
            assessments = assessment_sources(workspace, submission, item, criterion_id, member)
            if len(assessments) < MINIMUM_SPREAD_SCORES:
                continue
            marks = [score for score, _ in assessments]
            spread = max(marks) - min(marks)
            impact = spread / criterion['max_score'] * criterion['weight'] / total_weight * PERCENTAGE_SCALE
            if spread < thresholds['criterion_spread'] and impact < weighted_threshold:
                continue
            reconciled = include_verdict and item['proposal'] is not None
            next_step = (
                'Inspect the arbitration rationale in the Columns ledger.'
                if reconciled
                else 'Reconcile the marks against the rubric and evidence.'
            )
            findings.append(
                Finding(
                    finding_id(submission, 'score', criterion_id, member),
                    submission,
                    'Scoring',
                    f'{criterion["label"]} score disagreement',
                    f'Independent marks range from {min(marks):g} to {max(marks):g} of {criterion["max_score"]:g}.',
                    'grade_impact',
                    tuple(source for _, source in assessments),
                    next_step,
                    () if reconciled else default_options('Ask the arbiter to compare the evidence', 'arbiter'),
                    status='reconciled' if reconciled else 'open',
                    criterion=criterion_id,
                    member=member,
                    impact_percentage_points=impact,
                )
            )
    return findings


def sanity_findings(submission: str, item: ReviewItem) -> list[Finding]:
    verification = item['verification']
    if verification is None or not item['verification_current']:
        state = 'Missing' if verification is None else 'Stale'
        return [
            workflow_finding(
                submission,
                'sanity',
                'Verification',
                f'{state} sanity review',
                'The proposed verdict needs current checks before release.',
                'sanity',
                'Request a current sanity review',
            )
        ]
    findings: list[Finding] = []
    for index, check in enumerate(verification['checks']):
        if check['status'] == 'confirmed':
            continue
        severity: Severity = 'blocker' if check.get('grade_affecting', True) else 'advisory'
        reference = f'verification/{submission}.json#/checks/{index}'
        source = Source('sanity', reference, check['finding'], tuple(check['evidence']))
        findings.append(
            Finding(
                finding_id(submission, 'sanity-check', check['claim'], str(index)),
                submission,
                'Verification',
                f'{check["status"].capitalize()}: {check["claim"]}',
                check['finding'],
                severity,
                (source,),
                'Resolve the finding against the current verdict and re-check any correction.',
                default_options('Ask for a focused re-check', 'sanity'),
            )
        )
    for index, claim in enumerate(verification['unresolved']):
        reference = f'verification/{submission}.json#/unresolved/{index}'
        findings.append(
            workflow_finding(
                submission,
                f'unresolved-{index}',
                'Verification',
                'Unresolved sanity finding',
                claim,
                'sanity',
                'Resolve the outstanding evidence or policy question',
                (Source('sanity', reference, claim),),
            )
        )
    return findings


def coverage_findings(submission: str, item: ReviewItem, checks: Mapping[str, VerificationCheck]) -> list[Finding]:
    groups: defaultdict[tuple[str, str, str], list[tuple[str, Source]]] = defaultdict(list)
    for grader, record in item.get('coverage', {}).items():
        for index, observation in enumerate(record['observations']):
            status = observation['status']
            role = observation['role']
            environment = observation['environment']
            reference = f'coverage/{grader}/{submission}.json#/observations/{index}'
            source = Source(
                grader,
                reference,
                f'{status.capitalize()} · {observation["observed_at"]} · {observation["observation"]}',
                tuple(observation['evidence']),
            )
            groups[observation['check_id'], role, environment].append((status, source))

    findings: list[Finding] = []
    for (check_id, role, environment), observations in groups.items():
        statuses = {status for status, _ in observations}
        if statuses == {'passed'}:
            continue
        scenario = f'{check_id} · {role} · {environment}'
        mixed = {'passed', 'failed'} <= statuses
        check = checks[check_id]
        relevant = check['required'] or bool(check['criteria'])
        severity: Severity = 'grade_impact' if relevant else 'advisory'
        title = f'Mixed observations · {check_id}' if mixed else f'Coverage follow-up · {check_id}'
        findings.append(
            Finding(
                finding_id(submission, 'coverage-observation', check_id, role, environment),
                submission,
                'Verification coverage',
                title,
                f'{role} · {environment} · ' + ', '.join(sorted(statuses)),
                severity,
                tuple(source for _, source in observations),
                'Review the observations and re-run consequential or incomplete checks where access permits.',
                default_options('Request a focused verification check', 'grader'),
                scenario=scenario,
            )
        )
    return findings


def current_remote_attempts(workspace: Workspace) -> list[RemoteAttempt]:
    task_paths = sorted((workspace.root / 'remote_tasks').glob('*/*/task.json'))
    if not task_paths:
        return []
    current_revision = digest(workspace.prepared())
    issued = [(path, read_task(read_json(path))) for path in task_paths]
    return [
        RemoteAttempt(
            task,
            path.parent / 'result' / 'result.json',
            imported_result(workspace.root, task['task_id'], task['attempt']),
        )
        for path, task in issued
        if task['revision'] == current_revision
    ]


def remote_findings(workspace: Workspace, attempts: list[RemoteAttempt]) -> list[Finding]:
    current: dict[str, RemoteAttempt] = {}
    for attempt in attempts:
        task_id = attempt.task['task_id']
        if task_id not in current or attempt.task['attempt'] > current[task_id].task['attempt']:
            current[task_id] = attempt
    findings: list[Finding] = []
    for task_id, attempt in current.items():
        if attempt.result is None:
            continue
        result_reference = attempt.result_path.relative_to(workspace.root).as_posix()
        evidence_location = attempt.result_path.parent.relative_to(workspace.root).as_posix()
        submission = attempt.task['submission_id']
        for index, observation in enumerate(attempt.result['observations']):
            status = observation['status']
            if status not in ('blocked', 'untested'):
                continue
            check = observation['id']
            source = Source(
                task_id,
                f'{result_reference}#/observations/{index}',
                observation['observation'] or 'No observation was returned.',
                tuple(f'{evidence_location}/{item}' for item in observation['evidence']),
            )
            findings.append(
                Finding(
                    finding_id(submission, 'remote', task_id, str(attempt.task['attempt']), check),
                    submission,
                    'Remote checks',
                    f'{status.capitalize()} remote check · {check}',
                    f'{attempt.result["environment"]} · {observation["observation"] or "No observation was returned."}',
                    'advisory',
                    (source,),
                    'Check access or dispatch another attempt to a capable machine.',
                    default_options('Request another remote attempt', 'grader'),
                    scenario=f'{task_id} · {check}',
                )
            )
    return findings


def ordered(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            finding.status != 'open',
            SEVERITY_ORDER[finding.severity],
            -(finding.impact_percentage_points or 0),
            finding.topic.casefold(),
            finding.submission,
            finding.title.casefold(),
            finding.id,
        ),
    )


def collect_findings(
    workspace: Workspace, state: ReviewState, stage: Stage, attempts: list[RemoteAttempt] | None = None
) -> list[Finding]:
    selected_attempts = attempts if attempts is not None else current_remote_attempts(workspace)
    findings = remote_findings(workspace, selected_attempts)
    if stage == 'setup':
        return ordered(findings)
    include_verdict = stage in ('arbitration', 'sanity', 'release')
    checks = {check['id']: check for check in load_verification(workspace.root)}
    for submission, item in state.items():
        findings.extend(record_flags(workspace, submission, item, include_verdict))
        findings.extend(score_findings(workspace, submission, item, include_verdict))
        findings.extend(coverage_findings(submission, item, checks))
        if item['missing']:
            findings.append(
                workflow_finding(
                    submission,
                    'coverage',
                    'Grading coverage',
                    'Incomplete grader coverage',
                    'Missing ' + ', '.join(item['missing']) + '.',
                    'grader',
                    'Resume the missing grading passes',
                )
            )
        if missing_coverage := item.get('missing_coverage'):
            findings.append(
                workflow_finding(
                    submission,
                    'checklist',
                    'Grading coverage',
                    'Required checks missing',
                    'Missing observations from ' + ', '.join(missing_coverage) + '.',
                    'grader',
                    'Complete the required verification records',
                )
            )
        if include_verdict and item['proposal'] is None:
            findings.append(
                workflow_finding(
                    submission,
                    'arbitration',
                    'Arbitration',
                    'Missing arbitration',
                    'Independent assessments need a reconciled verdict.',
                    'arbiter',
                    'Complete arbitration',
                )
            )
        if stage in ('sanity', 'release') and item['proposal'] is not None:
            findings.extend(sanity_findings(submission, item))
        if stage == 'release' and item['ready'] and not item['approved']:
            findings.append(
                workflow_finding(
                    submission,
                    'approval',
                    'Human review',
                    'Awaiting current human approval',
                    'The verdict is ready for staff review.',
                    'ta',
                    'Review the current grade bundle',
                )
            )
    return ordered(findings)


def stage_progress(workspace: Workspace, state: ReviewState, stage: Stage) -> Progress:
    if stage == 'setup':
        return {'complete': len(state), 'total': len(state), 'label': 'Pinned submissions'}
    if stage == 'grading':
        expected = len(state) * len(workspace.config['graders'])
        missing = sum(len(item['missing']) for item in state.values())
        return {'complete': expected - missing, 'total': expected, 'label': 'Independent evaluations'}
    if stage == 'arbitration':
        label = 'Reconciled verdicts'
        complete = sum(item['proposal'] is not None for item in state.values())
    elif stage == 'sanity':
        label = 'Verdicts ready for human review'
        complete = sum(item['ready'] for item in state.values())
    else:
        label = 'Current human approvals'
        complete = sum(item['approved'] for item in state.values())
    return {'complete': complete, 'total': len(state), 'label': label}


def guide_stops(stage: Stage, progress: Progress, findings: list[Finding]) -> list[GuideStop]:
    open_findings = [item for item in findings if item.status == 'open']
    blockers = sum(item.severity == 'blocker' for item in open_findings)
    actionable = sum(bool(item.options) for item in open_findings)
    next_steps: dict[Stage, str] = {
        'setup': 'Launch the independent graders with the frozen rubric and verification checklist.',
        'grading': 'Hand complete independent evaluations and coverage records to the arbiter.',
        'arbitration': 'Send the current verdicts to the independent sanity reviewer.',
        'sanity': 'Review the verified grade bundles and record human approval.',
        'release': 'Export the approved gradebook and standalone student feedback.',
    }
    open_label = 'open finding' if len(open_findings) == 1 else 'open findings'
    blocker_label = 'blocker' if blockers == 1 else 'blockers'
    action_summary = (
        f'{len(open_findings)} {open_label} · {blockers} {blocker_label} · {actionable} requiring a choice.'
        if actionable
        else f'{len(open_findings)} {open_label} · {blockers} {blocker_label}.'
    )
    action_next_step = (
        'Choose one option for every open finding across all topics (Defer counts where offered; custom choices '
        'require written instructions). Copy or download the single complete JSON packet once all findings are '
        'answered, then import it with import-decisions.'
        if actionable
        else 'No open findings require a decision in this stage report.'
    )
    return [
        {
            'title': 'What finished',
            'summary': f'{progress["complete"]} of {progress["total"]} · {progress["label"]}.',
            'next_step': 'Open the Columns ledger to inspect individual scores, feedback and evidence.',
        },
        {
            'title': 'What needs attention',
            'summary': action_summary,
            'next_step': action_next_step,
        },
        {
            'title': 'Next in the workflow',
            'summary': next_steps[stage],
            'next_step': 'Resolve blockers for any submission you want to advance.' if blockers else '',
        },
    ]


def operator_stops(workspace: Workspace, attempts: list[RemoteAttempt]) -> list[GuideStop]:
    stops: list[GuideStop] = []
    revision_path = workspace.root / 'revision' / 'revision.json'
    if revision_path.is_file():
        revision = json_object(read_json(revision_path), 'Revision')
        submissions = json_object(revision['submissions'], 'Revision submissions')
        targets = [cast(SubmissionPlan, json_object(target, 'Submission plan')) for target in submissions.values()]
        carried = sum(len(target['carry']) for target in targets)
        reassess = sum(len(target['reassess']) for target in targets)
        review = sum(target['review'] for target in targets)
        carried_label = 'assessment' if carried == 1 else 'assessments'
        review_label = 'submission' if review == 1 else 'submissions'
        stops.append(
            {
                'title': 'Revision impact',
                'summary': (
                    f'{carried} {carried_label} carried forward · {reassess} scheduled for reassessment '
                    f'· {review} {review_label} to review.'
                ),
                'next_step': field_text(revision, 'reason', 'Revision'),
            }
        )
    if attempts:
        returned = [attempt.result for attempt in attempts if attempt.result is not None]
        blocked = sum(
            observation['status'] == 'blocked' for result in returned for observation in result['observations']
        )
        blocked_label = 'blocked observation' if blocked == 1 else 'blocked observations'
        stops.append(
            {
                'title': 'Remote checks',
                'summary': f'{len(returned)} of {len(attempts)} task attempts imported · {blocked} {blocked_label}.',
                'next_step': 'Review the imported observations and follow up on missing access or unfinished checks.',
            }
        )
    return stops


def make_draft(
    workspace: Workspace, state: ReviewState, stage: Stage, findings: list[Finding], attempts: list[RemoteAttempt]
) -> ReportDraft:
    progress = stage_progress(workspace, state, stage)
    run_id = digest(workspace.prepared())
    stops = guide_stops(stage, progress, findings) + operator_stops(workspace, attempts)
    source_digest = digest(
        {'run': run_id, 'stage': stage, 'state': state, 'stops': stops, 'findings': [asdict(item) for item in findings]}
    )
    return {
        'schema_version': REPORT_SCHEMA_VERSION,
        'run_id': run_id,
        'stage': stage,
        'source_digest': source_digest,
        'progress': progress,
        'stops': stops,
        'findings': [asdict(item) for item in findings],
    }


def stage_draft(workspace: Workspace, state: ReviewState, stage: str) -> ReportDraft:
    scope = stage_name(stage)
    attempts = current_remote_attempts(workspace)
    return make_draft(workspace, state, scope, collect_findings(workspace, state, scope, attempts), attempts)


def field_text(fields: Mapping[str, object], key: str, label: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GradingError(f'{label} {key} must be non-empty text')
    return value


def structured_options(data: object, label: str) -> tuple[Option, ...]:
    require(isinstance(data, list) and bool(data), f'{label}: expected at least one option')
    options: dict[str, Option] = {}
    for entry in cast(list[object], data):
        exact_keys(entry, ['id', 'label', 'consequence', 'owner'], label)
        fields = cast(dict[str, object], entry)
        option_id = identifier(fields['id'], f'{label} option id')
        require(option_id not in options, f'{label}: duplicate option {option_id}')
        owner = field_text(fields, 'owner', label)
        require(owner in ('ta', 'setup', 'grader', 'arbiter', 'sanity'), f'{label}: unknown option owner {owner}')
        options[option_id] = Option(
            option_id, field_text(fields, 'label', label), field_text(fields, 'consequence', label), owner
        )
    return tuple(options.values())


def curate_group(data: object, available: Mapping[str, Finding], workspace: Workspace) -> tuple[set[str], Finding]:
    required = {'finding_ids', 'topic', 'title', 'summary', 'next_step'}
    optional = {'severity', 'criterion', 'member', 'scenario', 'question', 'options'}
    require(isinstance(data, dict) and required <= data.keys() <= required | optional, 'Invalid curated finding fields')
    fields = cast(dict[str, object], data)
    strings(fields['finding_ids'], 'Curated finding IDs', 1)
    ids = cast(list[str], fields['finding_ids'])
    require(
        len(ids) == len(set(ids)) and all(item in available for item in ids),
        'Curated finding IDs must be unique and available',
    )
    originals = [available[item] for item in ids]
    first = originals[0]
    require(
        all(item.submission == first.submission and item.status == first.status for item in originals),
        'A finding group must have one submission and one status',
    )
    original_members = {item.member for item in originals if item.member is not None}
    original_criteria = {item.criterion for item in originals if item.criterion is not None}
    original_scenarios = {item.scenario for item in originals if item.scenario is not None}
    member = fields.get('member', next(iter(original_members), None))
    criterion = fields.get('criterion', next(iter(original_criteria), None))
    scenario = fields.get('scenario', next(iter(original_scenarios), None))
    require(not original_members or original_members == {member}, 'A finding group must describe one member')
    require(not original_criteria or original_criteria == {criterion}, 'A finding group must describe one criterion')
    require(
        scenario is None or isinstance(scenario, str) and bool(scenario.strip()), 'Invalid scenario in curated finding'
    )
    require(not original_scenarios or original_scenarios == {scenario}, 'A finding group must describe one scenario')
    require(
        member is None
        or isinstance(member, str)
        and member in {item['student_id'] for item in workspace.roster[first.submission]['members']},
        'Unknown member in curated finding',
    )
    require(
        criterion is None or isinstance(criterion, str) and criterion in workspace.criteria,
        'Unknown criterion in curated finding',
    )
    if isinstance(criterion, str):
        require(
            (workspace.criteria[criterion]['scope'] == 'individual') == (member is not None),
            'Curated finding member must match criterion scope',
        )
    original_severity = min((item.severity for item in originals), key=SEVERITY_ORDER.__getitem__)
    severity = fields.get('severity', original_severity)
    require(isinstance(severity, str) and severity in SEVERITY_ORDER, 'Unknown curated severity')
    severity = cast(Severity, severity)
    require(SEVERITY_ORDER[severity] <= SEVERITY_ORDER[original_severity], "Curation cannot lower a finding's severity")
    options = structured_options(fields['options'], 'Curated finding options') if 'options' in fields else first.options
    require(first.status == 'open' or not options, 'A reconciled finding cannot request another decision')
    impacts = [item.impact_percentage_points for item in originals if item.impact_percentage_points is not None]
    return set(ids), replace(
        first,
        id=finding_id('curated', *sorted(ids)),
        topic=field_text(fields, 'topic', 'Curated finding'),
        title=field_text(fields, 'title', 'Curated finding'),
        summary=field_text(fields, 'summary', 'Curated finding'),
        next_step=field_text(fields, 'next_step', 'Curated finding'),
        severity=severity,
        question=field_text(fields, 'question', 'Curated finding') if 'question' in fields else first.question,
        options=options,
        sources=tuple(dict.fromkeys(source for item in originals for source in item.sources)),
        impact_percentage_points=max(impacts, default=None),
        member=cast(str | None, member),
        criterion=cast(str | None, criterion),
        scenario=cast(str | None, scenario),
    )


def curated_content(
    workspace: Workspace, draft: ReportDraft, findings: list[Finding], curation: Path
) -> tuple[list[Finding], list[GuideStop]]:
    data: object = read_json(curation)
    require(
        isinstance(data, dict) and {'source_digest', 'groups'} <= data.keys() <= {'source_digest', 'groups', 'stops'},
        'Expected source_digest, groups, and optional stops in curation',
    )
    fields = cast(dict[str, object], data)
    require(fields['source_digest'] == draft['source_digest'], 'Curation is stale. Generate a fresh stage draft.')
    require(isinstance(fields['groups'], list), 'Curation groups must be a list')
    available = {item.id: item for item in findings}
    combined: list[Finding] = []
    for entry in cast(list[object], fields['groups']):
        consumed, finding = curate_group(entry, available, workspace)
        combined.append(finding)
        for key in consumed:
            del available[key]
    stops = fields.get('stops', [])
    require(isinstance(stops, list), 'Curation stops must be a list')
    polished_stops: list[GuideStop] = []
    for stop in cast(list[object], stops):
        exact_keys(stop, ['title', 'summary', 'next_step'], 'Guide stop')
        values = cast(dict[str, object], stop)
        polished_stops.append(
            {
                'title': field_text(values, 'title', 'Guide stop'),
                'summary': field_text(values, 'summary', 'Guide stop'),
                'next_step': field_text(values, 'next_step', 'Guide stop'),
            }
        )
    return ordered(combined + list(available.values())), polished_stops


def stage_report(workspace: Workspace, state: ReviewState, stage: str, curation: Path | None = None) -> Path:
    scope = stage_name(stage)
    attempts = current_remote_attempts(workspace)
    findings = collect_findings(workspace, state, scope, attempts)
    draft = make_draft(workspace, state, scope, findings, attempts)
    if curation is not None:
        findings, curated_stops = curated_content(workspace, draft, findings, curation)
        stops = guide_stops(scope, draft['progress'], findings) + operator_stops(workspace, attempts) + curated_stops
        draft = {**draft, 'findings': [asdict(item) for item in findings], 'stops': stops}
    body = {'title': workspace.config['title'], 'stage_label': STAGE_LABELS[scope], **draft}
    payload = {**body, 'review_digest': digest(body)}
    encoded = script_safe_json(payload)
    assets = workspace.root / 'report_assets'
    stylesheet = report_stylesheet(assets, 'stage.css')
    script = (assets / 'stage.js').read_text(encoding='utf-8')
    title = escape(f'{workspace.config["title"]} · {STAGE_LABELS[scope]} · Markwire')
    page = (
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title><style>{stylesheet}</style></head><body>'
        '<main id="stage-app"><noscript><p>Enable JavaScript to inspect this stage report and complete the decision '
        'hand-off.</p></noscript></main>'
        f'<script id="stage-data" type="application/json">{encoded}</script><script>{script}</script></body></html>'
    )
    target = workspace.root / 'reports' / f'{scope}.html'
    write_json(target.with_suffix('.json'), payload)
    write_text(target, page)
    return target


def normalise_decisions(value: object) -> list[Decision]:
    require(isinstance(value, list) and bool(value), 'Decision packet must contain at least one decision')
    decisions: dict[str, Decision] = {}
    for entry in cast(list[object], value):
        require(
            isinstance(entry, dict) and {'issueId', 'optionId'} <= entry.keys() <= {'issueId', 'optionId', 'note'},
            'Invalid decision fields',
        )
        fields = cast(dict[str, object], entry)
        issue = field_text(fields, 'issueId', 'Decision')
        option = field_text(fields, 'optionId', 'Decision')
        note = fields.get('note', '')
        require(isinstance(note, str), 'Decision note must be text')
        note = cast(str, note).strip()
        require(option != 'custom' or bool(note), 'Custom choices require written instructions in note')
        require(issue not in decisions, f'Duplicate decision for finding {issue}')
        decisions[issue] = {'issueId': issue, 'optionId': option, 'note': note}
    return [decisions[key] for key in sorted(decisions)]


def import_decisions(workspace: Workspace, state: ReviewState, response: Path, reviewer: str) -> Path:
    nonempty(reviewer, 'Reviewer')
    data: object = read_json(response)
    exact_keys(data, ['schemaVersion', 'runId', 'stage', 'reviewDigest', 'decisions'], 'Decision response')
    fields = cast(dict[str, object], data)
    require(
        type(fields['schemaVersion']) is int and fields['schemaVersion'] == REPORT_SCHEMA_VERSION,
        'Unsupported decision response version',
    )
    stage = stage_name(field_text(fields, 'stage', 'Decision response'))
    run_id = field_text(fields, 'runId', 'Decision response')
    review_digest = field_text(fields, 'reviewDigest', 'Decision response')
    decisions = normalise_decisions(fields['decisions'])
    identity = {
        'run_id': run_id,
        'stage': stage,
        'review_digest': review_digest,
        'reviewer': reviewer.strip(),
        'decisions': decisions,
    }
    receipt = workspace.root / 'decisions' / f'{digest(identity)}.json'
    if receipt.exists():
        return receipt
    persisted_path = workspace.root / 'reports' / f'{stage}.json'
    require(persisted_path.is_file(), 'Generate the corresponding stage report before importing decisions')
    persisted = json_object(read_json(persisted_path), 'Saved report')
    content = {key: value for key, value in persisted.items() if key != 'review_digest'}
    current = stage_draft(workspace, state, stage)
    require(
        persisted.get('review_digest') == review_digest == digest(content)
        and current['run_id'] == persisted.get('run_id') == run_id
        and current['source_digest'] == persisted.get('source_digest'),
        'Decision packet is stale or belongs to a different report. Generate and review the latest stage report.',
    )
    findings = {
        finding['id']: finding
        for finding in cast(list[SavedFinding], persisted['findings'])
        if finding['status'] == 'open' and finding['options']
    }
    actions = []
    for decision in decisions:
        finding = findings.get(decision['issueId'])
        if finding is None:
            raise GradingError(f'Unknown or closed finding {decision["issueId"]}')
        selected = next((option for option in finding['options'] if option['id'] == decision['optionId']), None)
        if selected is None:
            raise GradingError(f'Unknown option {decision["optionId"]} for {decision["issueId"]}')
        actions.append(
            {
                'issue_id': finding['id'],
                'submission': finding['submission'],
                'member': finding['member'],
                'criterion': finding['criterion'],
                'finding': finding['title'],
                'sources': finding['sources'],
                'owner': selected['owner'],
                'action': selected['label'],
                'consequence': selected['consequence'],
                'note': decision['note'],
            }
        )
    unanswered = findings.keys() - {decision['issueId'] for decision in decisions}
    require(
        not unanswered,
        'Answer every open finding with options before importing (partial packets are rejected). '
        f'Unanswered remediation questions: {len(unanswered)}.',
    )
    payload = {
        'schema_version': REPORT_SCHEMA_VERSION,
        **identity,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'actions': actions,
    }
    write_json(receipt, payload)
    return receipt
