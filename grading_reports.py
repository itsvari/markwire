"""Render staff review and approved student feedback from validated records."""

import csv
import html
import io
import statistics
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal, TypedDict

from grading_core import (
    Assessment,
    Criterion,
    Evaluation,
    GradingError,
    Member,
    ReviewItem,
    ReviewState,
    Verification,
    Workspace,
    write_json,
    write_text,
)
from grading_stage_reports import STAGES, collect_findings, report_stylesheet, script_safe_json


SPREAD_DECIMAL_PLACES = 2
MINIMUM_SPREAD_SCORES = 2
SpreadsheetCell = str | int | float | None


class CriterionRow(TypedDict):
    id: str
    key: str
    name: str
    max: int
    weight: int | float
    scope: Literal['individual', 'shared']
    member: str | None
    candidates: dict[str, Assessment | None]
    proposal: Assessment | None
    spread: int | float | None
    different: bool
    disputed: bool


class Totals(TypedDict):
    raw: int | float
    weighted: float


class LedgerMember(TypedDict):
    id: str
    name: str
    github: str
    totals: dict[str, Totals | None]
    proposed: Totals | None
    spread: int | float | None


class LedgerSource(TypedDict):
    repo: str
    branch: str
    commit: str


class FindingSource(TypedDict):
    author: str
    reference: str
    observation: str


class LedgerFinding(TypedDict):
    topic: str
    title: str
    summary: str
    severity: str
    status: str
    impact: float | None
    sources: list[FindingSource]


class CorrectionNote(TypedDict):
    reason: str
    evidence: list[str]


class LedgerSubmission(TypedDict):
    id: str
    type: Literal['group', 'individual']
    members: list[LedgerMember]
    rows: list[CriterionRow]
    approved: bool
    ready: bool
    missing: list[str]
    flags: list[str]
    findings: list[LedgerFinding]
    spread: int | float | None
    disputed: bool
    summary: str | None
    overall: dict[str, str | None]
    source: LedgerSource
    verification: Verification | None
    verificationCurrent: bool
    history: list[CorrectionNote]


class StudentAssessment(TypedDict):
    score: int | float
    feedback: str
    scope: str


class StudentFeedback(TypedDict):
    submission_id: str
    student_id: str
    name: str
    github_username: str
    members: list[Member]
    commit: str
    scores: dict[str, StudentAssessment]
    summary: str
    raw_total: int | float
    weighted_percentage: float


def escape_html(value: object) -> str:
    return html.escape(str(value), quote=True)


def csv_text(rows: Iterable[Iterable[SpreadsheetCell]]) -> str:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    for row in rows:
        # prevent spreadsheet applications from interpreting user text as formulas
        writer.writerow(
            [
                "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
                for value in row
            ]
        )
    return output.getvalue()


def gradebook(workspace: Workspace, state: ReviewState) -> str:
    labels = [f'{criterion["label"]} [{criterion["scope"]}]' for criterion in workspace.criteria.values()]
    rows: list[list[SpreadsheetCell]] = [
        [
            'Submission ID',
            'Student ID',
            'Name',
            'GitHub username',
            'Commit',
            *labels,
            'Raw total',
            'Weighted %',
            'Approved',
        ]
    ]
    for submission_id, item in state.items():
        verdict = item['proposal']
        if verdict is None:
            continue
        for member in item['submission']['members']:
            student_id = member['student_id']
            scores = workspace.assessments(verdict, student_id)
            raw_total, weighted_percentage = workspace.totals(verdict, student_id)
            rows.append(
                [
                    submission_id,
                    student_id,
                    member['name'],
                    member['github_username'],
                    verdict['commit'],
                    *[scores[key]['score'] for key in workspace.criteria],
                    raw_total,
                    weighted_percentage,
                    item['approved'],
                ]
            )
    return csv_text(rows)


def criterion_value(record: Evaluation | None, criterion_id: str, student_id: str | None) -> Assessment | None:
    if record is None:
        return None
    if student_id is None:
        return record['scores'][criterion_id]
    return record['individual_scores'][student_id][criterion_id]


def criterion_subjects(
    criteria: Mapping[str, Criterion],
    members: Sequence[Member],
) -> Iterable[tuple[str, Criterion, Member | None]]:
    for criterion_id, criterion in criteria.items():
        if criterion['scope'] == 'individual':
            for member in members:
                yield criterion_id, criterion, member
        else:
            yield criterion_id, criterion, None


def criterion_rows(
    workspace: Workspace,
    item: ReviewItem,
    disputed_subjects: set[tuple[str | None, str | None]],
) -> list[CriterionRow]:
    rows: list[CriterionRow] = []
    graders = workspace.config['graders']
    for index, (criterion_id, criterion, member) in enumerate(
        criterion_subjects(workspace.criteria, item['submission']['members']),
    ):
        student_id = member['student_id'] if member else None
        candidates = {
            grader: criterion_value(item['evaluations'].get(grader), criterion_id, student_id) for grader in graders
        }
        available = [assessment for assessment in candidates.values() if assessment is not None]
        scores = [assessment['score'] for assessment in available]
        signatures = {
            (assessment['score'], assessment['feedback'], tuple(assessment['evidence'])) for assessment in available
        }
        rows.append(
            {
                'id': f'r{index}',
                'key': criterion_id,
                'name': criterion['label'],
                'max': criterion['max_score'],
                'weight': criterion['weight'],
                'scope': 'individual' if member else 'shared',
                'member': student_id,
                'candidates': candidates,
                'proposal': criterion_value(item['proposal'], criterion_id, student_id),
                'spread': max(scores) - min(scores) if len(scores) >= MINIMUM_SPREAD_SCORES else None,
                'different': len(signatures) > 1 or bool(item['missing']),
                'disputed': (criterion_id, student_id) in disputed_subjects,
            }
        )
    return rows


def comparison_rows(submission_id: str, submission: LedgerSubmission) -> Iterable[list[SpreadsheetCell]]:
    members = {member['id']: member for member in submission['members']}
    for criterion in submission['rows']:
        candidates = [
            assessment['score'] if assessment is not None else None for assessment in criterion['candidates'].values()
        ]
        scores = [score for score in candidates if score is not None]
        student_id = criterion['member']
        member = members.get(student_id) if student_id else None
        verdict = criterion['proposal']
        yield [
            submission_id,
            criterion['key'],
            *candidates,
            round(statistics.mean(scores), SPREAD_DECIMAL_PLACES) if scores else '',
            round(statistics.pstdev(scores), SPREAD_DECIMAL_PLACES) if scores else '',
            criterion['spread'],
            verdict['score'] if verdict is not None else '',
            criterion['disputed'] or bool(submission['missing']),
            criterion['scope'],
            student_id or '',
            member['name'] if member else '',
            member['github'] if member else '',
        ]


def member_totals(workspace: Workspace, evaluation: Evaluation | None, student_id: str) -> Totals | None:
    if evaluation is None:
        return None
    raw_total, weighted_percentage = workspace.totals(evaluation, student_id)
    return {'raw': raw_total, 'weighted': weighted_percentage}


def ledger_members(workspace: Workspace, item: ReviewItem) -> list[LedgerMember]:
    members: list[LedgerMember] = []
    for member in item['submission']['members']:
        student_id = member['student_id']
        totals = {
            grader: member_totals(workspace, item['evaluations'].get(grader), student_id)
            for grader in workspace.config['graders']
        }
        available = [total['raw'] for total in totals.values() if total is not None]
        members.append(
            {
                'id': student_id,
                'name': member['name'],
                'github': member['github_username'],
                'totals': totals,
                'proposed': member_totals(workspace, item['proposal'], student_id),
                'spread': max(available) - min(available) if len(available) >= MINIMUM_SPREAD_SCORES else None,
            }
        )
    return members


def submission_flags(
    graders: Sequence[str],
    item: ReviewItem,
    rows: Sequence[CriterionRow],
    members: Mapping[str, LedgerMember],
    total_spread_threshold: int | float,
) -> list[str]:
    flags = [f'Missing {grader}' for grader in item['missing']]
    for grader in graders:
        evaluation = item['evaluations'].get(grader)
        if evaluation is not None:
            flags.extend(f'{grader}: {flag}' for flag in evaluation['flags'])
    verdict = item['proposal']
    if verdict is not None:
        flags.extend(f'Effective verdict: {flag}' for flag in verdict['flags'])
    for criterion in rows:
        if not criterion['disputed']:
            continue
        student_id = criterion['member']
        subject = members[student_id]['name'] if student_id else 'Shared submission'
        flags.append(f'{subject}: {criterion["name"]} spread {criterion["spread"]}')
    for member in members.values():
        spread = member['spread']
        if spread is not None and spread >= total_spread_threshold:
            flags.append(f'{member["name"]}: total spread {spread}')
    if verdict is None:
        flags.append('Missing arbitration')
    verification = item['verification']
    if verification is None:
        flags.append('Missing sanity review')
    else:
        if not item['verification_current']:
            flags.append('Stale sanity review. Regenerate after changes.')
        flags.extend(verification['unresolved'])
        flags.extend(
            f'Unverified: {check["claim"]}' for check in verification['checks'] if check['status'] == 'unverified'
        )
    if not item['approved']:
        flags.append('Awaiting current human approval')
    return flags


def ledger_submission(
    workspace: Workspace,
    submission_id: str,
    item: ReviewItem,
    findings: list[LedgerFinding],
    disputed_subjects: set[tuple[str | None, str | None]],
    commit: str,
) -> LedgerSubmission:
    source = item['submission']
    graders = workspace.config['graders']
    threshold = workspace.config['review_thresholds']['total_spread']
    rows = criterion_rows(workspace, item, disputed_subjects)
    members = ledger_members(workspace, item)
    spreads = [member['spread'] for member in members if member['spread'] is not None]
    overall: dict[str, str | None] = {grader: None for grader in graders}
    for grader in graders:
        evaluation = item['evaluations'].get(grader)
        if evaluation is not None:
            overall[grader] = evaluation['summary']
    verdict = item['proposal']
    return {
        'id': submission_id,
        'type': 'group' if len(members) > 1 else 'individual',
        'members': members,
        'rows': rows,
        'approved': item['approved'],
        'ready': item['ready'],
        'missing': item['missing'],
        'flags': submission_flags(graders, item, rows, {member['id']: member for member in members}, threshold),
        'findings': findings,
        'spread': max(spreads) if spreads else None,
        'disputed': any(row['disputed'] for row in rows) or any(spread >= threshold for spread in spreads),
        'summary': verdict['summary'] if verdict is not None else None,
        'overall': overall,
        'source': {'repo': source['repo_url'], 'branch': source['branch'], 'commit': commit},
        'verification': item['verification'],
        'verificationCurrent': item['verification_current'],
        'history': [{'reason': change['reason'], 'evidence': change['evidence']} for change in item['history']],
    }


def report(workspace: Workspace, state: ReviewState) -> None:
    """Render the Columns ledger from validated, effective assessment records."""
    graders = workspace.config['graders']
    manifest = workspace.prepared()
    findings: dict[str, list[LedgerFinding]] = {submission_id: [] for submission_id in state}
    disputed_subjects: dict[str, set[tuple[str | None, str | None]]] = {submission_id: set() for submission_id in state}
    for finding in collect_findings(workspace, state, 'release'):
        findings[finding.submission].append(
            {
                'topic': finding.topic,
                'title': finding.title,
                'summary': finding.summary,
                'severity': finding.severity,
                'status': finding.status,
                'impact': finding.impact_percentage_points,
                'sources': [
                    {'author': source.author, 'reference': source.reference, 'observation': source.observation}
                    for source in finding.sources
                ],
            }
        )
        if finding.impact_percentage_points is not None:
            disputed_subjects[finding.submission].add((finding.criterion, finding.member))
    comparison: list[list[SpreadsheetCell]] = [
        [
            'Submission ID',
            'Criterion',
            *graders,
            'Mean',
            'Population SD',
            'Spread',
            'Proposed score',
            'Review needed',
            'Scope',
            'Student ID',
            'Name',
            'GitHub username',
        ]
    ]
    submissions: list[LedgerSubmission] = []
    for submission_id, item in state.items():
        entry = ledger_submission(
            workspace,
            submission_id,
            item,
            findings[submission_id],
            disputed_subjects[submission_id],
            manifest['submissions'][submission_id]['commit'],
        )
        submissions.append(entry)
        comparison.extend(comparison_rows(submission_id, entry))

    reports = workspace.root / 'reports'
    stage_guides = [
        {'stage': stage, 'href': f'{stage}.html'} for stage in STAGES if (reports / f'{stage}.html').is_file()
    ]
    title = workspace.config['title']
    payload = {
        'title': title,
        'maximum': sum(criterion['max_score'] for criterion in workspace.criteria.values()),
        'thresholds': workspace.config['review_thresholds'],
        'stageGuides': stage_guides,
        'graders': [{'id': grader, 'name': grader, 'code': f'G{index + 1}'} for index, grader in enumerate(graders)],
        'submissions': submissions,
    }
    encoded = script_safe_json(payload)
    assets = workspace.root / 'report_assets'
    stylesheet = report_stylesheet(assets, 'columns.css')
    script = (assets / 'columns.js').read_text(encoding='utf-8')
    page = (
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="dark"><title>{escape_html(title)} · Markwire</title>'
        f'<style>{stylesheet}</style></head><body class="columns">'
        '<div id="app"><noscript><p class="empty-state">Enable JavaScript to inspect the interactive staff ledger. '
        'The comparison and proposed-grades CSV files contain the numerical results.</p></noscript></div>'
        '<div id="announcer" class="sr-only" aria-live="polite"></div>'
        f'<script id="report-data" type="application/json">{encoded}</script><script>{script}</script></body></html>'
    )
    write_text(reports / 'review.html', page)
    write_text(reports / 'comparison.csv', csv_text(comparison))
    write_text(reports / 'proposed-grades.csv', gradebook(workspace, state))
    write_json(reports / 'state.json', state)


def feedback_document(title: str, content: str, stylesheet: str) -> str:
    return (
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="dark"><title>{escape_html(title)}</title>'
        f'<style>{stylesheet}</style></head><body><main id="top">{content}</main></body></html>'
    )


def member_handle(member: Member) -> str:
    handle = member['github_username']
    return f'<span class="github">@{escape_html(handle)}</span>' if handle else ''


def members_html(members: Sequence[Member]) -> str:
    entries = ''.join(
        f'<li><b>{escape_html(member["name"])}</b> {member_handle(member)}'
        f' <span class="member-id">{escape_html(member["student_id"])}</span></li>'
        for member in members
    )
    return f'<ul class="members">{entries}</ul>'


def student_feedback(
    workspace: Workspace,
    verdict: Evaluation,
    submission_id: str,
    member: Member,
    members: list[Member],
) -> StudentFeedback:
    student_id = member['student_id']
    raw_total, weighted_percentage = workspace.totals(verdict, student_id)
    assessments = workspace.assessments(verdict, student_id)
    return {
        'submission_id': submission_id,
        'student_id': student_id,
        'name': member['name'],
        'github_username': member['github_username'],
        'members': members,
        'commit': verdict['commit'],
        'scores': {
            criterion_id: {
                'score': assessments[criterion_id]['score'],
                'feedback': assessments[criterion_id]['feedback'],
                'scope': criterion['scope'],
            }
            for criterion_id, criterion in workspace.criteria.items()
        },
        'summary': verdict['summary'],
        'raw_total': raw_total,
        'weighted_percentage': weighted_percentage,
    }


def feedback_score_row(criterion: Criterion, assessment: StudentAssessment, multiple_members: bool) -> str:
    scope = 'Shared' if multiple_members else 'Submission'
    if assessment['scope'] == 'individual':
        scope = 'Individual'
    return (
        f'<tr><th scope="row"><span class="scope">{scope}</span><br>{escape_html(criterion["label"])}</th>'
        f'<td>{escape_html(assessment["score"])} / {escape_html(criterion["max_score"])}</td>'
        f'<td>{escape_html(assessment["feedback"])}</td></tr>'
    )


def feedback_section(
    criteria: Mapping[str, Criterion],
    member: Member,
    feedback: StudentFeedback,
    maximum_score: int,
) -> str:
    name = escape_html(member['name'])
    members = feedback['members']
    submission_id = escape_html(feedback['submission_id'])
    student_id = escape_html(feedback['student_id'])
    score_rows = ''.join(
        feedback_score_row(criteria[criterion_id], assessment, len(members) > 1)
        for criterion_id, assessment in feedback['scores'].items()
    )
    members_heading = 'Submission members' if len(members) > 1 else 'Submission member'
    overall_heading = 'Overall remark · shared submission' if len(members) > 1 else 'Overall remark · submission'
    return (
        f'<article id="{submission_id}--{student_id}"><div class="eyebrow">Approved feedback · {submission_id}</div>'
        f'<h2>{name} {member_handle(member)}</h2>'
        f'<p class="student-grade">{escape_html(feedback["weighted_percentage"])}% · '
        f'{escape_html(feedback["raw_total"])} / {escape_html(maximum_score)}</p>'
        f'<h3>{members_heading}</h3>{members_html(members)}'
        f'<p class="source">Graded snapshot {escape_html(feedback["commit"])}</p>'
        f'<section class="overall"><h3>{overall_heading}</h3>'
        f'<p>{escape_html(feedback["summary"])}</p></section>'
        '<p class="total-note">Your total combines shared submission criteria with your individual criteria. '
        'Shared feedback applies to every member of the submission; individual feedback below applies only to you.</p>'
        f'<div class="scroll" tabindex="0" role="region" aria-label="Feedback for {name}">'
        '<table class="student-table"><thead><tr><th scope="col">Criterion</th><th scope="col">Score</th>'
        f'<th scope="col">Feedback</th></tr></thead><tbody>{score_rows}</tbody></table></div></article>'
    )


def export_feedback(workspace: Workspace, state: ReviewState, destination: Path | None = None) -> None:
    """Export only shared and recipient-owned scores."""
    if destination is None:
        destination = workspace.root / 'exports'
    students: list[StudentFeedback] = []
    sections: list[str] = []
    maximum_score = sum(criterion['max_score'] for criterion in workspace.criteria.values())
    stylesheet = report_stylesheet(workspace.root / 'report_assets', 'feedback.css')
    assignment_title = workspace.config['title']
    heading = f'<h1>{escape_html(assignment_title)}</h1>'
    for submission_id, item in state.items():
        verdict = item['proposal']
        if verdict is None:
            raise GradingError(f'{submission_id}: an effective verdict is required for feedback')
        members = item['submission']['members']
        for member in members:
            feedback = student_feedback(workspace, verdict, submission_id, member, members)
            section = feedback_section(workspace.criteria, member, feedback, maximum_score)
            document_title = f'{assignment_title} · {member["name"]}'
            write_text(
                destination / 'feedback' / submission_id / f'{member["student_id"]}.html',
                feedback_document(document_title, heading + section, stylesheet),
            )
            students.append(feedback)
            sections.append(section)

    write_json(destination / 'student-feedback.json', students)
    archive = (
        f'<header class="masthead"><div class="eyebrow">Private approved feedback archive</div>{heading}'
        '<p class="intro">This private archive contains feedback for the entire roster. Do not share this file '
        "with students; distribute only each recipient's standalone file from "
        'feedback/SUBMISSION_ID/STUDENT_ID.html, which contains shared submission feedback and '
        "that student's own individual feedback.</p></header>" + ''.join(sections)
    )
    write_text(
        destination / 'student-feedback.html', feedback_document(f'{assignment_title} feedback', archive, stylesheet)
    )
