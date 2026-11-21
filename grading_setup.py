"""Use the same setup files for the terminal wizard and coding agents."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypeVar, TypedDict, cast

from grading_core import (
    DEFAULT_ROUNDING_DIGITS,
    PERCENTAGE_SCALE,
    Assignment,
    Criterion,
    GradingError,
    Workspace,
    exact_keys,
    identifier,
    json_object,
    nonempty,
    parse_assignment,
    read_json,
    require,
    write_json,
    write_text,
)

VERSION = 1
PREFERENCES_PATH = Path('private/run-preferences.json')
VERIFICATION_PATH = Path('input/verification.json')
STAGES = ('setup', 'grading', 'arbitration', 'sanity', 'release')
MODEL_ROLES = ('arbiter', 'sanity', 'report')
SUBAGENT_TASKS = ('evidence', 'vision', 'feedback_draft', 'feedback_edit')
DEFAULT_CONCURRENCY = 3
DEFAULT_MINIMUM_WORKERS = 1
DEFAULT_WEIGHTED_REVIEW_POINTS = 1
ILLUSTRATIVE_SCORE_FRACTION = 0.5
ASSIGNMENT_DOCUMENTS = ('brief.md', 'rubric.md', 'policy.md')
Choice = TypeVar('Choice', bound=str)
Delegation = Literal['off', 'as_needed', 'required']
Authentication = Literal['off', 'human', 'preauthorised']
ReportMode = Literal['off', 'blockers', 'selected']
DELEGATION_MODES: tuple[Delegation, ...] = ('off', 'as_needed', 'required')
AUTH_MODES: tuple[Authentication, ...] = ('off', 'human', 'preauthorised')
REPORT_MODES: tuple[ReportMode, ...] = ('off', 'blockers', 'selected')


class ModelSelection(TypedDict):
    provider: str
    model: str
    options: NotRequired[dict[str, str | bool]]


class RolePreferences(TypedDict):
    subagents: Delegation
    minimum_workers_per_submission: int
    max_concurrent_workers: int
    task_models: dict[str, ModelSelection]
    browser: bool
    desktop: bool
    auth: Authentication


class ReportPreferences(TypedDict):
    mode: ReportMode
    stages: list[str]


class Machine(TypedDict):
    environment_id: str | None
    capabilities: list[str]


class Preferences(TypedDict):
    version: int
    orchestration: Literal['manual', 't3']
    models: dict[str, ModelSelection]
    roles: dict[str, RolePreferences]
    reports: ReportPreferences
    machines: dict[str, Machine]


class VerificationCheck(TypedDict):
    id: str
    instructions: str
    criteria: list[str]
    capabilities: list[str]
    required: bool


def default_preferences() -> Preferences:
    return {
        'version': VERSION,
        'orchestration': 'manual',
        'models': {},
        'roles': {},
        'reports': {'mode': 'off', 'stages': []},
        'machines': {},
    }


def role_defaults() -> RolePreferences:
    return {
        'subagents': 'off',
        'minimum_workers_per_submission': 0,
        'max_concurrent_workers': 0,
        'task_models': {},
        'browser': False,
        'desktop': False,
        'auth': 'off',
    }


def distinct_identifiers(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise GradingError(f'{label} must be a list')
    names = [identifier(item, label) for item in cast(list[object], value)]
    require(len(set(names)) == len(names), f'{label} contains duplicates')
    return names


def validate_model(value: object, label: str) -> ModelSelection:
    selection = json_object(value, label)
    require({'provider', 'model'} <= selection.keys(), f'{label} requires provider and model')
    require(selection.keys() <= {'provider', 'model', 'options'}, f'{label} has unexpected fields')
    nonempty(selection['provider'], f'{label} provider')
    nonempty(selection['model'], f'{label} model')
    if 'options' in selection:
        options = json_object(selection['options'], f'{label} options')
        for option, setting in options.items():
            nonempty(option, f'{label} option name')
            require(isinstance(setting, (str, bool)), f'{label} options must map names to strings or booleans')
    return cast(ModelSelection, selection)


def validate_role(value: object, label: str) -> None:
    role = exact_keys(value, role_defaults().keys(), label)
    require(role['subagents'] in DELEGATION_MODES, f'{label}: invalid subagent mode')
    minimum_workers = validate_worker_count(role['minimum_workers_per_submission'], label)
    concurrent_workers = validate_worker_count(role['max_concurrent_workers'], label)
    require(
        (minimum_workers > 0) == (role['subagents'] == 'required'),
        f'{label}: required delegation needs a positive minimum, otherwise use zero',
    )
    require(
        (concurrent_workers > 0) == (role['subagents'] != 'off'),
        f'{label}: enabled delegation needs a positive concurrency cap, otherwise use zero',
    )
    task_models = json_object(role['task_models'], f'{label} task models')
    require(task_models.keys() <= set(SUBAGENT_TASKS), f'{label}: invalid task model slots')
    require(role['subagents'] != 'off' or not task_models, f'{label}: task models require delegation')
    for task, model in task_models.items():
        validate_model(model, f'{label} {task}')
    require(
        type(role['browser']) is bool and type(role['desktop']) is bool,
        f'{label}: browser and desktop must be booleans',
    )
    require(role['auth'] in AUTH_MODES, f'{label}: invalid authentication mode')
    require(
        role['auth'] == 'off' or role['browser'] or role['desktop'],
        f'{label}: authentication requires browser or desktop access',
    )


def validate_worker_count(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise GradingError(f'{label}: worker counts must be non-negative integers')
    return value


def validate_preferences(value: object, graders: Sequence[str]) -> Preferences:
    preferences = exact_keys(value, default_preferences().keys(), str(PREFERENCES_PATH))
    require(
        type(preferences['version']) is int and preferences['version'] == VERSION, 'Unsupported run preferences version'
    )
    require(preferences['orchestration'] in ('manual', 't3'), 'Orchestration must be manual or t3')
    model_roles = {*graders, *MODEL_ROLES}
    models = json_object(preferences['models'], 'Model selections')
    require(models.keys() <= model_roles, 'Unknown model role in run preferences')
    for role, selection in models.items():
        validate_model(selection, f'{role} model')
    roles = json_object(preferences['roles'], 'Role preferences')
    require(roles.keys() <= model_roles | {'grader'}, 'Unknown tool or subagent role in run preferences')
    for role, policy in roles.items():
        validate_role(policy, role)
    reports = exact_keys(preferences['reports'], ('mode', 'stages'), 'Report preferences')
    require(reports['mode'] in REPORT_MODES, 'Report mode must be off, blockers or selected')
    stages = distinct_identifiers(reports['stages'], 'Report stages')
    require(not set(stages) - set(STAGES), 'Unknown report stage')
    require(bool(stages) == (reports['mode'] == 'selected'), 'Select explicit stages only for selected report mode')
    machines = json_object(preferences['machines'], 'Machines')
    for name, settings in machines.items():
        validate_machine(name, settings)
    return cast(Preferences, preferences)


def validate_machine(name: str, settings: object) -> None:
    identifier(name, 'Machine name')
    machine = exact_keys(settings, ('environment_id', 'capabilities'), f'Machine {name}')
    if machine['environment_id'] is not None:
        nonempty(machine['environment_id'], f'{name} environment_id')
    distinct_identifiers(machine['capabilities'], f'{name} capabilities')


def load_preferences(root: Path, graders: Sequence[str]) -> Preferences:
    path = root / PREFERENCES_PATH
    return validate_preferences(read_json(path), graders) if path.is_file() else default_preferences()


def report_requested(preferences: Preferences, stage: str, *, blocker: bool = False) -> bool:
    require(stage in STAGES, f'Unknown report stage: {stage}')
    reports = preferences['reports']
    return (reports['mode'] == 'blockers' and blocker) or (reports['mode'] == 'selected' and stage in reports['stages'])


def prompt_preferences(preferences: Preferences, role: str, grader: str | None) -> str:
    roles = preferences['roles']
    policy = roles.get(grader or '', roles.get(role, role_defaults()))
    browser = 'permitted when the assignment policy allows it' if policy['browser'] else 'disabled'
    desktop = 'permitted when the assignment policy allows it' if policy['desktop'] else 'disabled'
    authentication = {
        'off': 'Authenticated testing is disabled.',
        'human': 'Request human-assisted sign-in on the assigned machine.',
        'preauthorised': 'Use only the operator-authorised accounts and sessions for authenticated tests.',
    }[policy['auth']]
    text = f'Browser access is {browser}. Desktop control is {desktop}. {authentication}'
    delegation = policy['subagents']
    if delegation == 'off':
        return text + '\nSubagent delegation is disabled for this role.'
    if delegation == 'required':
        minimum = policy['minimum_workers_per_submission']
        text += f'\nUse at least {minimum} subagent worker(s) per assigned submission.'
    else:
        text += '\nUse subagents as needed and where available.'
    text += (
        f' Run at most {policy["max_concurrent_workers"]} concurrently and give workers separate work and evidence paths. '
        'Apply these same tool and authentication limits to delegated work.'
    )
    for task, selection in policy['task_models'].items():
        options = (
            f' with options {json.dumps(selection["options"], sort_keys=True)}' if selection.get('options') else ''
        )
        text += f' Use {selection["provider"]}/{selection["model"]}{options} for {task.replace("_", " ")}.'
    text += " Review the returned work and own this role's final record."
    return text + ' Flag any unmet delegation requirement or unavailable configured worker model to the TA.'


def load_verification(root: Path, criteria: Sequence[str] | None = None) -> list[VerificationCheck]:
    path = root / VERIFICATION_PATH
    if not path.is_file():
        return []
    value = exact_keys(read_json(path), ('version', 'checks'), str(VERIFICATION_PATH))
    require(type(value['version']) is int and value['version'] == VERSION, 'Unsupported verification checklist version')
    return validate_verification(value['checks'], criteria)


def validate_verification(value: object, criteria: Sequence[str] | None) -> list[VerificationCheck]:
    if not isinstance(value, list):
        raise GradingError('Verification checks must be a list')
    checks = [validate_verification_check(check, criteria) for check in cast(list[object], value)]
    seen: set[str] = set()
    for check in checks:
        require(check['id'] not in seen, f'Duplicate verification check: {check["id"]}')
        seen.add(check['id'])
    return checks


def validate_verification_check(value: object, criteria: Sequence[str] | None) -> VerificationCheck:
    check = exact_keys(value, ('id', 'instructions', 'criteria', 'capabilities', 'required'), 'Verification check')
    name = identifier(check['id'], 'Verification check ID')
    nonempty(check['instructions'], f'Check {name} instructions')
    selected_criteria = distinct_identifiers(check['criteria'], f'Check {name} criteria')
    require(criteria is None or set(selected_criteria) <= set(criteria), f'Check {name}: unknown rubric criterion')
    distinct_identifiers(check['capabilities'], f'Check {name} capabilities')
    require(type(check['required']) is bool, f'Check {name}: required must be a boolean')
    return cast(VerificationCheck, check)


def prompt_verification(root: Path) -> str:
    if not load_verification(root):
        return ''
    return (
        'Follow the verification checklist in input/verification.json. Apply each required check relevant to '
        'the assigned criteria and each submission. Independently record the environment, role, expected '
        'and observed behaviour, evidence, and status (passed, failed, blocked or untested) for each check. '
        'Flag access blocks or missing capabilities explicitly.'
    )


def ask(label: str, default: str | None = None) -> str:
    suffix = f' [{default}]' if default else ''
    result = input(f'  {label}{suffix} > ').strip()
    return result or default or ''


def choose(label: str, choices: Sequence[Choice], default: str) -> Choice:
    while True:
        chosen = ask(f'{label} ({" / ".join(choices)})', default)
        for choice in choices:
            if chosen == choice:
                return choice
        print(f'  Choose one of {", ".join(choices)}.')


def yes(label: str, default: bool = False) -> bool:
    return choose(label, ('yes', 'no'), 'yes' if default else 'no') == 'yes'


def comma_separated_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(',') if item.strip()]


def section(title: str, description: str) -> None:
    print(f'\n{title}\n  {description}')


def editable_list(label: str, previous: Sequence[str]) -> list[str]:
    value = ask(f'{label} (none clears)', ', '.join(previous))
    return [] if value == 'none' else comma_separated_values(value)


def ask_model(role: str, existing: ModelSelection | None = None) -> ModelSelection | None:
    current = f'{existing["provider"]}/{existing["model"]}' if existing else None
    selection = ask(f'{role} provider/model (skip clears)', current)
    if not selection or selection == 'skip':
        return None
    provider, _, model = selection.partition('/')
    selected: ModelSelection = {'provider': provider, 'model': model}
    if existing and selected == {key: existing[key] for key in ('provider', 'model')} and 'options' in existing:
        selected['options'] = existing['options']
    return validate_model(selected, role)


def ask_roles(graders: Sequence[str], current: Preferences) -> Preferences:
    preferences: Preferences = {
        **current,
        'models': dict(current['models']),
        'roles': dict(current['roles']),
        'machines': dict(current['machines']),
    }
    orchestration = choose('Launch mode', ('manual', 't3'), current['orchestration'])
    preferences['orchestration'] = 't3' if orchestration == 't3' else 'manual'
    if yes('Configure model selections', orchestration == 't3'):
        for role in (*graders, *MODEL_ROLES):
            selection = ask_model(role, current['models'].get(role))
            preferences['models'].pop(role, None)
            if selection is not None:
                preferences['models'][role] = selection
    valid_roles = ('grader', *graders, *MODEL_ROLES)
    while role := ask(f'Edit role permissions ({", ".join(valid_roles)}; blank to continue)'):
        require(role in valid_roles, f'Unknown role: {role}')
        fallback = current['roles'].get('grader', role_defaults()) if role in graders else role_defaults()
        preferences['roles'][role] = ask_role(preferences['roles'].get(role, fallback))
    return preferences


def ask_role(previous: RolePreferences) -> RolePreferences:
    mode = choose('Subagents', DELEGATION_MODES, previous['subagents'])
    minimum_workers = 0
    concurrent_workers = 0
    models: dict[str, ModelSelection] = {}
    if mode == 'required':
        minimum_workers = int(
            ask(
                'Minimum workers per submission',
                str(previous['minimum_workers_per_submission'] or DEFAULT_MINIMUM_WORKERS),
            )
        )
    if mode != 'off':
        concurrent_workers = int(
            ask(
                'Maximum concurrent workers',
                str(previous['max_concurrent_workers'] or DEFAULT_CONCURRENCY),
            )
        )
        models = ask_specialist_models(previous['task_models'])
    browser = yes('Allow browser access', previous['browser'])
    desktop = yes('Allow desktop control', previous['desktop'])
    authentication = choose('Authenticated testing', AUTH_MODES, previous['auth']) if browser or desktop else 'off'
    return {
        'subagents': mode,
        'minimum_workers_per_submission': minimum_workers,
        'max_concurrent_workers': concurrent_workers,
        'task_models': models,
        'browser': browser,
        'desktop': desktop,
        'auth': authentication,
    }


def ask_specialist_models(previous: dict[str, ModelSelection]) -> dict[str, ModelSelection]:
    if not yes('Set specialist worker models'):
        return previous
    models: dict[str, ModelSelection] = {}
    for task in SUBAGENT_TASKS:
        selection = ask_model(task.replace('_', ' '), previous.get(task))
        if selection is not None:
            models[task] = selection
    return models


def ask_reporting(previous: ReportPreferences) -> ReportPreferences:
    mode = choose('Intermediate guides', REPORT_MODES, previous['mode'])
    if mode != 'selected':
        return {'mode': mode, 'stages': []}
    stages = comma_separated_values(ask(f'Stages ({", ".join(STAGES)})', ', '.join(previous['stages'])))
    return {'mode': mode, 'stages': stages}


def ask_machines(previous: dict[str, Machine]) -> dict[str, Machine]:
    machines = dict(previous)
    while name := ask('Machine alias to add or edit (blank to continue)'):
        identifier(name, 'Machine name')
        current = machines.get(name, {'environment_id': None, 'capabilities': []})
        if name in machines and yes(f'Remove {name} from the machine registry'):
            del machines[name]
            continue
        environment = ask('T3 environment ID (manual clears)', current['environment_id'])
        capabilities = editable_list(
            'Capabilities, e.g. browser, desktop, human-auth, windows, linux, macos', current['capabilities']
        )
        machines[name] = {
            'environment_id': environment if environment and environment != 'manual' else None,
            'capabilities': capabilities,
        }
    return machines


def ask_verification(root: Path) -> list[VerificationCheck]:
    checks = {check['id']: check for check in load_verification(root)}
    while name := ask('Verification check ID to add or edit (blank to continue)'):
        previous = checks.get(name)
        if previous is not None and yes(f'Remove check {name}'):
            del checks[name]
            continue
        instructions = ask(
            'Expected scenario, commands and observations', previous['instructions'] if previous else None
        )
        criteria = editable_list('Relevant criterion keys (empty means all)', previous['criteria'] if previous else [])
        capabilities = editable_list('Required capabilities', previous['capabilities'] if previous else [])
        checks[name] = {
            'id': name,
            'instructions': instructions,
            'criteria': criteria,
            'capabilities': capabilities,
            'required': yes('Required verification', previous['required'] if previous else True),
        }
    return list(checks.values())


def ask_inputs(previous: Assignment) -> tuple[Assignment, dict[str, str]]:
    section(
        'Rubric and roster', 'Start from official documents and a normalised roster. See SETUP.md for column mapping.'
    )
    documents = {}
    for name in (*ASSIGNMENT_DOCUMENTS, 'roster.csv'):
        if source := ask(f'{name} replacement path (blank keeps input/{name})'):
            documents[name] = Path(source).expanduser().read_text(encoding='utf-8')
    assignment_id = ask('Assignment ID', previous['assignment_id'])
    title = ask('Title', previous['title'])
    graders = comma_separated_values(ask('Grader aliases', ', '.join(previous['graders'])))
    criteria = ask_rubric(previous['criteria'])
    rounding_digits = int(
        ask('Decimal places in the final percentage', str(previous.get('rounding_digits', DEFAULT_ROUNDING_DIGITS)))
    )
    thresholds = dict(previous['review_thresholds'])
    thresholds['weighted_percentage_points'] = float(
        ask(
            'Weighted disagreement threshold in percentage points',
            str(thresholds.get('weighted_percentage_points', DEFAULT_WEIGHTED_REVIEW_POINTS)),
        )
    )
    current = previous['snapshot_policy']
    mode = choose('Git source selection', ('explicit', 'head', 'deadline'), current['mode'])
    deadline = (
        ask('Cutoff with time zone, e.g. 2026-12-01T17:00:00+00:00', current['deadline'])
        if mode == 'deadline'
        else None
    )
    boundary = (
        choose('Cutoff boundary', ('exclusive', 'inclusive'), current['boundary']) if deadline else current['boundary']
    )
    timestamp = (
        choose('Commit timestamps', ('committer', 'both'), current['timestamp']) if deadline else current['timestamp']
    )
    snapshot_policy = {**current, 'mode': mode, 'deadline': deadline, 'boundary': boundary, 'timestamp': timestamp}
    baseline_required = yes('Requires starter files in input/baseline', previous['baseline_required'])
    assignment = parse_assignment(
        {
            **previous,
            'assignment_id': assignment_id,
            'title': title,
            'graders': graders,
            'criteria': criteria,
            'rounding_digits': rounding_digits,
            'review_thresholds': thresholds,
            'snapshot_policy': snapshot_policy,
            'baseline_required': baseline_required,
        }
    )
    return assignment, documents


def ask_rubric(criteria: list[Criterion]) -> list[Criterion]:
    candidates = []
    for criterion in criteria:
        if yes(f'Keep criterion {criterion["key"]}', True):
            candidates.append(ask_criterion(criterion['key'], criterion))
    while key := ask('Add criterion key (blank to continue)'):
        candidates.append(
            ask_criterion(
                key,
                {
                    'key': key,
                    'label': key,
                    'max_score': 1,
                    'weight': 1,
                    'guidance': 'See input/rubric.md',
                    'scope': 'unresolved',
                },
            )
        )
    return candidates


def ask_criterion(key: str, previous: Criterion) -> Criterion:
    existing = previous.get('allowed_scores', [])
    allowed_scores = comma_separated_values(
        ask('Allowed scores (blank means whole marks, whole clears)', ', '.join(map(str, existing))),
    )
    criterion: Criterion = {
        'key': key,
        'label': ask('Criterion label', previous['label']),
        'max_score': int(ask('Maximum mark', str(previous['max_score']))),
        'weight': float(ask('Relative weight', str(previous['weight']))),
        'guidance': ask('Rubric anchor or guidance reference', previous['guidance']),
        'scope': choose('Scope', ('submission', 'individual', 'unresolved'), previous['scope']),
    }
    if allowed_scores and allowed_scores != ['whole']:
        criterion['allowed_scores'] = [float(score) for score in allowed_scores]
    return criterion


def review(root: Path) -> None:
    workspace = Workspace(root)
    workspace.require_scopes()
    require(workspace.roster, 'Add submissions to input/roster.csv')
    for name in ASSIGNMENT_DOCUMENTS:
        nonempty((root / 'input' / name).read_text(encoding='utf-8'), f'input/{name}')
    if workspace.config['baseline_required']:
        baseline = root / 'input/baseline'
        require(
            any(
                path.is_file() and '.git' not in path.relative_to(baseline).parts and path.name != '.gitkeep'
                for path in baseline.rglob('*')
            ),
            'Copy the required starter files to input/baseline/',
        )
    checks = load_verification(root, tuple(workspace.criteria))
    preferences = load_preferences(root, workspace.config['graders'])
    section(
        'Review before freezing',
        f'{len(workspace.roster)} submissions, {len(workspace.criteria)} criteria, {len(checks)} verification checks.',
    )
    section('Worked grade', 'One illustrative score per criterion, using the course weights.')
    example: list[str] = []
    weighted_percentage_points = 0.0
    total_weight = sum(criterion['weight'] for criterion in workspace.criteria.values())
    for criterion in workspace.criteria.values():
        maximum, weight = criterion['max_score'], criterion['weight']
        middle = maximum * ILLUSTRATIVE_SCORE_FRACTION
        allowed = criterion.get('allowed_scores')
        score = min(allowed, key=lambda mark: abs(mark - middle)) if allowed else math.floor(middle)
        contribution_percentage_points = score / maximum * weight / total_weight * PERCENTAGE_SCALE
        weighted_percentage_points += contribution_percentage_points
        example.append(
            f'  {criterion["key"]}: {score:g}/{maximum:g} at {weight / total_weight:.1%} '
            f'weight = {contribution_percentage_points:g} points'
        )
    print('\n'.join(example))
    rounding_digits = workspace.config.get('rounding_digits', DEFAULT_ROUNDING_DIGITS)
    print(f'  Total = {round(weighted_percentage_points, rounding_digits):g}%')
    section(
        'Run preferences',
        f'{preferences["orchestration"]} launch, {preferences["reports"]["mode"]} guides, '
        f'{len(preferences["machines"])} remote machines.',
    )


def setup(root: Path, *, review_only: bool = False) -> None:
    if review_only:
        review(root)
        return
    require(not (root / 'manifest.json').exists(), 'Use a workspace revision to change frozen assignment inputs')
    previous_assignment = parse_assignment(read_json(root / 'assignment.json'))
    previous_preferences = load_preferences(root, previous_assignment['graders'])
    try:
        print('Markwire assignment setup')
        assignment, documents = ask_inputs(previous_assignment)
        section('Verification', 'Define the shared floor. Required checks apply independently to every grader.')
        checks = ask_verification(root)
        section('Agents', 'Set tool access, specialist models and nested delegation per role or grader alias.')
        current_aliases = {*assignment['graders'], *MODEL_ROLES}
        preferences = ask_roles(
            assignment['graders'],
            {
                **previous_preferences,
                'models': {
                    role: model for role, model in previous_preferences['models'].items() if role in current_aliases
                },
                'roles': {
                    role: policy
                    for role, policy in previous_preferences['roles'].items()
                    if role == 'grader' or role in current_aliases
                },
            },
        )
        section('Reports and machines', 'Choose guide checkpoints and declare optional remote workers.')
        preferences = {
            **preferences,
            'reports': ask_reporting(preferences['reports']),
            'machines': ask_machines(preferences['machines']),
        }
        validate_preferences(preferences, assignment['graders'])
        validate_verification(checks, [item['key'] for item in assignment['criteria']])
        for name, contents in documents.items():
            write_text(root / 'input' / name, contents)
        write_json(root / 'assignment.json', {**assignment, 'policy_confirmed': False})
        write_json(root / VERIFICATION_PATH, {'version': VERSION, 'checks': checks})
        write_json(root / PREFERENCES_PATH, preferences)
        review(root)
        if yes('Confirm the rubric, roster and policy for preparation'):
            write_json(root / 'assignment.json', {**assignment, 'policy_confirmed': True})
            print('  Confirmed. Run python grading.py prepare when the inputs are ready.')
        else:
            print('  Draft saved. Complete setup before running prepare.')
    except (EOFError, KeyboardInterrupt) as error:
        raise GradingError('Setup stopped') from error
