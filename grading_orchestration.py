"""Keep the durable run plan for the optional T3 operator agent."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from grading_bundles import imported_result, read_task_specification
from grading_core import (
    GradingError,
    ReviewState,
    Workspace,
    digest,
    exact_keys,
    identifier,
    json_object,
    nonempty,
    read_json,
    require,
    write_json,
)
from grading_setup import ModelSelection, distinct_identifiers, load_preferences, role_defaults, validate_model
from grading_sources import inspect_snapshots

Stage = Literal['grading', 'arbitration', 'sanity']
ActiveStatus = Literal['reserved', 'running']
TerminalStatus = Literal['completed', 'failed', 'cancelled', 'interrupted']
STAGES: tuple[Stage, ...] = ('grading', 'arbitration', 'sanity')
ACTIVE_STATUSES: tuple[ActiveStatus, ...] = ('reserved', 'running')
TERMINAL_STATUSES: tuple[TerminalStatus, ...] = ('completed', 'failed', 'cancelled', 'interrupted')
RUN_VERSION = 1


class Slot(TypedDict):
    stage: Stage
    targets: list[str]
    selection: ModelSelection
    environment_id: str | None
    machine: str | None
    model_role: str
    spec: str | None
    spec_digest: str | None


class Attempt(TypedDict):
    number: int
    request_id: str
    targets: list[str]
    status: ActiveStatus | TerminalStatus
    task_id: str | None
    thread_id: str | None
    link: str | None
    executed_model: str | None


class Run(TypedDict):
    version: int
    revision: str
    run_id: str
    targets: list[str]
    slots: dict[str, Slot]
    attempts: dict[str, list[Attempt]]


class QueuedTask(TypedDict):
    slot: str
    status: ActiveStatus | Literal['retry', 'available']
    attempt: NotRequired[Attempt]
    targets: NotRequired[list[str]]


class RunStatus(TypedDict):
    run_id: str
    revision: str
    stage: Stage | Literal['human_review']
    tasks: list[QueuedTask]
    unresolved_submissions: list[str]


class DispatchTarget(TypedDict):
    providerInstanceId: str
    model: str
    options: NotRequired[dict[str, str | bool]]
    environmentId: NotRequired[str]


class DispatchRequest(TypedDict):
    clientRequestId: str
    mode: Literal['async']
    target: DispatchTarget
    submission_ids: list[str]
    state: ActiveStatus | TerminalStatus
    task_id: str | None
    thread_id: str | None
    link: str | None
    attempt: int
    bundle_spec: str | None
    bundle_spec_digest: str | None
    machine: str | None
    model_role: str
    dispatch: Literal['manual', 't3']


class OrchestrationArguments(argparse.Namespace):
    workspace: Path
    command: str
    run: str
    slot: str
    request_id: str
    submission: list[str]
    spec: Path
    machine: str
    model_role: str
    before: Stage
    retry: bool
    task_id: str
    thread_id: str
    link: str | None
    status: TerminalStatus
    executed_model: str | None


def run_path(root: Path, run_id: str) -> Path:
    return root / 'private' / 'orchestration' / f'{identifier(run_id, "run ID")}.json'


def model_for(models: Mapping[str, ModelSelection], role: str) -> ModelSelection:
    require(role in models, f'Configure an explicit T3 provider and model for {role}')
    return models[role]


def start(workspace: Workspace, run_id: str, targets: Sequence[str]) -> Run:
    path = run_path(workspace.root, run_id)
    manifest = workspace.prepared()
    selected = sorted(set(targets or workspace.roster))
    require(set(selected) <= set(workspace.roster), 'Run contains an unknown submission')
    if path.exists():
        previous = load_run(workspace, run_id)
        require(previous['targets'] == selected, 'Run ID already exists with different targets')
        return previous
    graders = workspace.config['graders']
    preferences = load_preferences(workspace.root, graders)
    require(preferences['orchestration'] == 't3', 'Enable T3 in the local run preferences')
    models = preferences['models']
    slots: dict[str, Slot] = {}
    for role in (*graders, 'arbiter', 'sanity'):
        stage: Stage = 'grading'
        if role == 'arbiter':
            stage = 'arbitration'
        elif role == 'sanity':
            stage = 'sanity'
        slots[role] = {
            'stage': stage,
            'targets': selected,
            'selection': model_for(models, role),
            'environment_id': None,
            'machine': None,
            'model_role': role,
            'spec': None,
            'spec_digest': None,
        }
    run: Run = {
        'version': RUN_VERSION,
        'revision': digest(manifest),
        'run_id': run_id,
        'targets': selected,
        'slots': slots,
        'attempts': {},
    }
    write_json(path, run)
    return run


def load_run(workspace: Workspace, run_id: str) -> Run:
    path = run_path(workspace.root, run_id)
    require(path.is_file(), f'Unknown orchestration run {run_id}')
    run = exact_keys(read_json(path), Run.__annotations__, 'Orchestration journal')
    require(
        type(run['version']) is int and run['version'] == RUN_VERSION and run['run_id'] == run_id,
        'Invalid orchestration journal',
    )
    require(run['revision'] == digest(workspace.prepared()), 'Orchestration run belongs to another revision')
    targets = distinct_identifiers(run['targets'], 'Run submission IDs')
    require(set(targets) <= workspace.roster.keys(), 'Run contains an unknown submission')
    slots = json_object(run['slots'], 'Task slots')
    for slot_name, slot in slots.items():
        validate_slot(slot_name, slot, targets)
    attempts = json_object(run['attempts'], 'Task attempts')
    require(attempts.keys() <= slots.keys(), 'Unregistered task in orchestration journal')
    for slot_name, slot_attempts in attempts.items():
        validate_attempts(slot_attempts, targets, slot_name)
    return cast(Run, run)


def validate_slot(slot_name: str, value: object, targets: Sequence[str]) -> None:
    identifier(slot_name, 'Task slot')
    slot = exact_keys(value, Slot.__annotations__, f'Task slot {slot_name}')
    require(slot['stage'] in STAGES, f'{slot_name}: Unknown orchestration stage')
    identifier(slot['model_role'], 'Model role')
    validate_model(slot['selection'], f'{slot_name} model selection')
    validate_scope(slot['targets'], targets, slot_name)
    for field in ('environment_id', 'machine', 'spec', 'spec_digest'):
        if slot[field] is not None:
            nonempty(slot[field], f'{slot_name} {field}')


def validate_attempts(value: object, targets: Sequence[str], slot_name: str) -> None:
    if not isinstance(value, list):
        raise GradingError(f'{slot_name}: Task attempts must be a list')
    for attempt_number, attempt_value in enumerate(cast(list[object], value), start=1):
        attempt = exact_keys(attempt_value, Attempt.__annotations__, f'{slot_name} attempt')
        require(
            type(attempt['number']) is int and attempt['number'] == attempt_number,
            f'{slot_name}: Nonconsecutive attempt number',
        )
        require(attempt['status'] in (*ACTIVE_STATUSES, *TERMINAL_STATUSES), f'{slot_name}: Unknown attempt status')
        nonempty(attempt['request_id'], 'Dispatch request ID')
        validate_scope(attempt['targets'], targets, slot_name)
        for field in ('task_id', 'thread_id', 'link', 'executed_model'):
            if attempt[field] is not None:
                nonempty(attempt[field], f'{slot_name} {field}')


def validate_scope(value: object, targets: Sequence[str], label: str) -> None:
    selected = distinct_identifiers(value, f'{label} submission IDs')
    require(bool(selected) and set(selected) <= set(targets), f'{label}: Invalid submission scope')


def add_probe(
    workspace: Workspace,
    run: Run,
    specification_path: Path,
    machine_name: str,
    model_role: str,
    stage: Stage,
) -> None:
    specification_path = specification_path.expanduser().resolve()
    specification = read_task_specification(specification_path)
    slot_name = f'probe-{specification.task_id}-{specification.attempt}'
    require(specification.submission_id in run['targets'], 'Probe submission is outside this run')
    preferences = load_preferences(workspace.root, workspace.config['graders'])
    machine = preferences['machines'].get(machine_name)
    if machine is None:
        raise GradingError(f'Unknown machine {machine_name}')
    capabilities = set(specification.capabilities)
    require(capabilities <= set(machine['capabilities']), 'Machine lacks required capabilities')
    role = 'grader' if model_role in workspace.config['graders'] else model_role
    roles = preferences['roles']
    policy = roles.get(model_role, roles.get(role, role_defaults()))
    require('browser' not in capabilities or policy['browser'], f'{model_role} does not permit browser access')
    require('desktop' not in capabilities or policy['desktop'], f'{model_role} does not permit desktop control')
    slot: Slot = {
        'stage': stage,
        'targets': [specification.submission_id],
        'selection': model_for(preferences['models'], model_role),
        'environment_id': machine['environment_id'],
        'machine': machine_name,
        'model_role': model_role,
        'spec': str(specification_path),
        'spec_digest': specification.fingerprint,
    }
    if slot_name in run['slots']:
        require(
            run['slots'][slot_name] == slot, f'Probe {specification.task_id} is already registered with another scope'
        )
        return
    for existing_name, existing in run['slots'].items():
        if STAGES.index(existing['stage']) >= STAGES.index(stage) and run['attempts'].get(existing_name):
            raise GradingError('Register a probe before dispatching work in the stage it gates')
    run['slots'][slot_name] = slot
    write_json(run_path(workspace.root, run['run_id']), run)


def latest_attempt(run: Run, slot_name: str) -> Attempt | None:
    attempts = run['attempts'].get(slot_name, [])
    return attempts[-1] if attempts else None


def attempt_active(run: Run, slot_name: str) -> bool:
    attempt = latest_attempt(run, slot_name)
    return attempt is not None and attempt['status'] in ACTIVE_STATUSES


def pending_targets(root: Path, run: Run, state: ReviewState) -> dict[str, list[str]]:
    pending: dict[str, list[str]] = {}
    for slot_name, slot in run['slots'].items():
        if slot['spec'] is not None:
            pending[slot_name] = [] if probe_imported(root, slot) else slot['targets']
        elif slot['stage'] == 'grading':
            pending[slot_name] = [
                target
                for target in slot['targets']
                if slot_name in state[target]['missing'] or slot_name in state[target]['missing_coverage']
            ]
        elif slot['stage'] == 'arbitration':
            pending[slot_name] = [target for target in slot['targets'] if state[target]['proposal'] is None]
        else:
            pending[slot_name] = [target for target in slot['targets'] if not state[target]['verification_current']]
    return pending


def run_status(workspace: Workspace, run: Run) -> RunStatus:
    state = workspace.state()
    pending = pending_targets(workspace.root, run, state)
    stage = active_stage(run, pending)
    tasks: list[QueuedTask] = []
    for slot_name in run['slots']:
        attempt = latest_attempt(run, slot_name)
        attempt_status = attempt['status'] if attempt else None
        if attempt is not None and attempt_status in ('reserved', 'running'):
            tasks.append({'slot': slot_name, 'status': attempt_status, 'attempt': attempt})
        elif runnable(run, slot_name, stage, pending):
            tasks.append(
                {'slot': slot_name, 'status': 'retry' if attempt else 'available', 'targets': pending[slot_name]}
            )
    blockers = [target for target in run['targets'] if not state[target]['ready']] if stage is None else []
    return {
        'run_id': run['run_id'],
        'revision': run['revision'],
        'stage': stage or 'human_review',
        'tasks': tasks,
        'unresolved_submissions': blockers,
    }


def active_stage(run: Run, pending: Mapping[str, list[str]]) -> Stage | None:
    return next(
        (
            stage
            for stage in STAGES
            if any(
                slot['stage'] == stage and (pending[slot_name] or attempt_active(run, slot_name))
                for slot_name, slot in run['slots'].items()
            )
        ),
        None,
    )


def pending_probes(run: Run, stage: Stage, pending: Mapping[str, list[str]]) -> bool:
    return any(
        slot['stage'] == stage and slot['spec'] is not None and (pending[slot_name] or attempt_active(run, slot_name))
        for slot_name, slot in run['slots'].items()
    )


def runnable(run: Run, slot_name: str, stage: Stage | None, pending: Mapping[str, list[str]]) -> bool:
    if stage is None or not pending[slot_name]:
        return False
    slot = run['slots'][slot_name]
    if slot['spec'] is not None:
        return STAGES.index(stage) <= STAGES.index(slot['stage'])
    return slot['stage'] == stage and not pending_probes(run, stage, pending)


def reserve(workspace: Workspace, run: Run, slot_name: str, *, retry: bool) -> DispatchRequest:
    require(slot_name in run['slots'], f'Unknown task slot {slot_name}')
    slot = run['slots'][slot_name]
    attempt = latest_attempt(run, slot_name)
    if attempt is not None and attempt['status'] in ACTIVE_STATUSES:
        require(not retry, 'Settle the current attempt before retrying')
        return dispatch_request(slot, attempt)
    pending = pending_targets(workspace.root, run, workspace.state())
    require(
        runnable(run, slot_name, active_stage(run, pending), pending),
        'Task is outside the runnable stage or already complete',
    )
    require(retry == (attempt is not None), 'Use --retry for a new attempt after a terminal child result')
    attempt_number = (attempt['number'] if attempt else 0) + 1
    client_request_id = 'grading-' + digest([run['revision'], run['run_id'], slot_name, attempt_number])
    current: Attempt = {
        'number': attempt_number,
        'request_id': client_request_id,
        'targets': pending[slot_name],
        'status': 'reserved',
        'task_id': None,
        'thread_id': None,
        'link': None,
        'executed_model': None,
    }
    run['attempts'][slot_name] = [*run['attempts'].get(slot_name, []), current]
    write_json(run_path(workspace.root, run['run_id']), run)
    return dispatch_request(slot, current)


def dispatch_request(slot: Slot, attempt: Attempt) -> DispatchRequest:
    selection = slot['selection']
    target: DispatchTarget = {'providerInstanceId': selection['provider'], 'model': selection['model']}
    if selection.get('options'):
        target['options'] = selection['options']
    if slot['environment_id']:
        target['environmentId'] = slot['environment_id']
    return {
        'clientRequestId': attempt['request_id'],
        'mode': 'async',
        'target': target,
        'submission_ids': attempt['targets'],
        'state': attempt['status'],
        'task_id': attempt['task_id'],
        'thread_id': attempt['thread_id'],
        'link': attempt['link'],
        'attempt': attempt['number'],
        'bundle_spec': slot['spec'],
        'bundle_spec_digest': slot['spec_digest'],
        'machine': slot['machine'],
        'model_role': slot['model_role'],
        'dispatch': 'manual' if slot['spec'] and slot['environment_id'] is None else 't3',
    }


def attach(
    workspace: Workspace,
    run: Run,
    slot_name: str,
    request_id: str,
    task_id: str,
    thread_id: str,
    link: str | None,
) -> None:
    current = latest_attempt(run, slot_name)
    if current is None:
        raise GradingError('Reserve the task before attaching a delegate')
    require(current['request_id'] == request_id, 'Delegate refers to a different attempt')
    if current['task_id'] is not None:
        require(
            current['task_id'] == task_id and current['thread_id'] == thread_id,
            'Attempt already has a different delegate',
        )
        return
    require(current['status'] == 'reserved', 'Attempt is not awaiting dispatch')
    run['attempts'][slot_name][-1] = {
        **current,
        'task_id': task_id,
        'thread_id': thread_id,
        'link': link,
        'status': 'running',
    }
    write_json(run_path(workspace.root, run['run_id']), run)


def finish(
    workspace: Workspace,
    run: Run,
    slot_name: str,
    request_id: str,
    status: TerminalStatus,
    executed_model: str | None,
) -> None:
    current = latest_attempt(run, slot_name)
    if current is None:
        raise GradingError('Reserve the task before recording its result')
    require(current['request_id'] == request_id, 'Delegate result refers to a different attempt')
    if current['status'] == status:
        return
    require(current['status'] in ACTIVE_STATUSES, 'Attempt already has a different terminal status')
    if status == 'completed':
        require(
            not pending_targets(workspace.root, run, workspace.state())[slot_name],
            'Expected validated outputs are still missing',
        )
        manifest = workspace.prepared()
        inspect_snapshots(
            workspace, {'submissions': {target: manifest['submissions'][target] for target in current['targets']}}
        )
    run['attempts'][slot_name][-1] = {**current, 'status': status, 'executed_model': executed_model}
    write_json(run_path(workspace.root, run['run_id']), run)


def probe_imported(root: Path, slot: Slot) -> bool:
    specification_path = slot['spec']
    if specification_path is None:
        return False
    specification = read_task_specification(Path(specification_path))
    require(specification.fingerprint == slot['spec_digest'], 'Registered probe instructions changed')
    return imported_result(root, specification.task_id, specification.attempt) is not None


def parse_arguments() -> OrchestrationArguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default=Path.cwd(), type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('start', 'status', 'add-probe', 'reserve', 'attach', 'finish'):
        subcommand = commands.add_parser(command)
        subcommand.add_argument('run')
        if command in ('reserve', 'attach', 'finish'):
            subcommand.add_argument('slot')
        if command in ('attach', 'finish'):
            subcommand.add_argument('--request-id', required=True)
        if command == 'start':
            subcommand.add_argument('--submission', action='append', default=[])
        elif command == 'add-probe':
            subcommand.add_argument('--spec', type=Path, required=True)
            subcommand.add_argument('--machine', required=True)
            subcommand.add_argument('--model-role', required=True)
            subcommand.add_argument('--before', choices=STAGES, required=True)
        elif command == 'reserve':
            subcommand.add_argument('--retry', action='store_true')
        elif command == 'attach':
            subcommand.add_argument('--task-id', required=True)
            subcommand.add_argument('--thread-id', required=True)
            subcommand.add_argument('--link')
        elif command == 'finish':
            subcommand.add_argument('--status', choices=TERMINAL_STATUSES, required=True)
            subcommand.add_argument('--executed-model')
    arguments = OrchestrationArguments()
    parser.parse_args(namespace=arguments)
    return arguments


def main() -> None:
    arguments = parse_arguments()
    workspace = Workspace(arguments.workspace)
    if arguments.command == 'start':
        run = start(workspace, arguments.run, arguments.submission)
    else:
        run = load_run(workspace, arguments.run)
    result: DispatchRequest | RunStatus
    if arguments.command == 'reserve':
        result = reserve(workspace, run, arguments.slot, retry=arguments.retry)
    else:
        if arguments.command == 'add-probe':
            add_probe(workspace, run, arguments.spec, arguments.machine, arguments.model_role, arguments.before)
        elif arguments.command == 'attach':
            attach(
                workspace,
                run,
                arguments.slot,
                arguments.request_id,
                arguments.task_id,
                arguments.thread_id,
                arguments.link,
            )
        elif arguments.command == 'finish':
            finish(workspace, run, arguments.slot, arguments.request_id, arguments.status, arguments.executed_model)
        result = run_status(workspace, run)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (GradingError, OSError, ValueError, KeyError, TypeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
