"""Package scoped remote observations without transferring the grading workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict, cast

BUNDLE_VERSION = 1
TASK_NAME = 'task.json'
RESULT_NAME = 'result.json'
BYTES_PER_MEBIBYTE = 1024 * 1024
MAX_MANIFEST_BYTES = 16 * BYTES_PER_MEBIBYTE
SHA256_HEX_PATTERN = re.compile(r'[0-9a-f]{64}')
ARCHIVE_IDENTIFIER_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*')
WINDOWS_DEVICE_NAMES = frozenset(
    {
        'con',
        'prn',
        'aux',
        'nul',
        'conin$',
        'conout$',
        *(f'{prefix}{suffix}' for prefix in ('com', 'lpt') for suffix in '123456789¹²³'),
    }
)
NON_PORTABLE_PATH_CHARACTERS = frozenset('\\:<>"|?*')
FIRST_NON_CONTROL_CHARACTER = 32
HAS_UNIX_FILE_MODES = os.name != 'nt'

ObservationStatus = Literal['passed', 'failed', 'blocked', 'untested']
OBSERVATION_STATUSES = ('passed', 'failed', 'blocked', 'untested')
COMPLETED_OBSERVATION_STATUSES = ('passed', 'failed')


class BundleError(ValueError):
    pass


class Check(TypedDict):
    id: str
    instructions: str


class FileEntry(TypedDict):
    sha256: str
    executable: bool


class SourceReference(TypedDict):
    commit: str
    tree: str


class Task(TypedDict):
    version: int
    task_id: str
    attempt: int
    submission_id: str
    assignment_id: str
    revision: str
    source: SourceReference
    capabilities: list[str]
    checks: list[Check]
    outputs: list[str]
    files: dict[str, FileEntry]


class Observation(TypedDict):
    id: str
    status: ObservationStatus
    observation: str
    evidence: list[str]


class Result(TypedDict):
    version: int
    task_id: str
    attempt: int
    revision: str
    task_digest: str
    environment: str
    observations: list[Observation]
    outputs: list[str]
    files: dict[str, FileEntry]


class ImportReceipt(TypedDict):
    task_digest: str
    result_digest: str


@dataclass(frozen=True)
class TaskSpecification:
    task_id: str
    attempt: int
    submission_id: str
    capabilities: tuple[str, ...]
    requested_files: tuple[str, ...]
    include_snapshot: bool
    checks: tuple[Check, ...]
    outputs: tuple[str, ...]
    fingerprint: str


def require_bundle(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def record(
    value: object,
    required_fields: set[str],
    label: str,
    optional_fields: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or not required_fields <= value.keys()
        or bool(value.keys() - required_fields - optional_fields)
    ):
        raise BundleError(f'{label}: unexpected or missing fields')
    return cast(dict[str, object], value)


def list_value(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise BundleError(f'{label}: expected a list')
    return cast(list[object], value)


def text_value(value: object, label: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or not blank and not value.strip():
        raise BundleError(f'{label}: expected non-empty text')
    return value


def identifier(value: object, label: str) -> str:
    text = text_value(value, label)
    require_bundle(ARCHIVE_IDENTIFIER_PATTERN.fullmatch(text) is not None, f'{label}: invalid identifier')
    return text


def attempt_number(value: object) -> int:
    if type(value) is not int or value < 1:
        raise BundleError('attempt must be a positive integer')
    return value


def path_name(value: object) -> str:
    name = text_value(value, 'bundle path')
    path = PurePosixPath(name)
    require_bundle(
        bool(path.parts)
        and not path.is_absolute()
        and path.as_posix() == name
        and all(
            part not in ('.', '..')
            and not part.endswith(('.', ' '))
            and part.split('.', 1)[0].casefold() not in WINDOWS_DEVICE_NAMES
            for part in path.parts
        )
        and not any(
            character in NON_PORTABLE_PATH_CHARACTERS or ord(character) < FIRST_NON_CONTROL_CHARACTER
            for character in name
        ),
        f'Unsafe bundle path: {name}',
    )
    return name


def path_identity(name: str) -> str:
    return unicodedata.normalize('NFC', name).casefold()


def is_linked_path(path: Path) -> bool:
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    file_attributes: object = getattr(attributes, 'st_file_attributes', None)
    if isinstance(file_attributes, int):
        return bool(file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(attributes.st_mode)


def regular_file(root: Path, name: str) -> Path:
    parts = PurePosixPath(path_name(name)).parts
    path = root.joinpath(*parts)
    require_bundle(
        all(not is_linked_path(root.joinpath(*parts[:length])) for length in range(1, len(parts) + 1))
        and path.is_file(),
        f'Expected a regular file without symlinks: {name}',
    )
    return path


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def parse_json(data: bytes) -> object:
    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        fields = dict(pairs)
        require_bundle(len(fields) == len(pairs), 'Duplicate JSON field')
        return fields

    def invalid_constant(constant: str) -> object:
        raise BundleError(f'Invalid JSON constant {constant}')

    return json.loads(data, object_pairs_hook=unique_fields, parse_constant=invalid_constant)


def json_digest(value: object) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def file_checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_index(files: Mapping[str, Path]) -> dict[str, FileEntry]:
    entries: dict[str, FileEntry] = {
        name: {
            'sha256': file_checksum(path),
            'executable': HAS_UNIX_FILE_MODES and bool(path.stat().st_mode & stat.S_IXUSR),
        }
        for name, path in sorted(files.items())
    }
    check_unique_paths(entries)
    return entries


def files_match(expected: Mapping[str, FileEntry], actual: Mapping[str, FileEntry]) -> bool:
    if HAS_UNIX_FILE_MODES:
        return expected == actual
    return {name: entry['sha256'] for name, entry in expected.items()} == {
        name: entry['sha256'] for name, entry in actual.items()
    }


def check_unique_paths(names: Mapping[str, object]) -> None:
    identities = {path_identity(name) for name in names}
    require_bundle(len(identities) == len(names), 'Archive paths collide on a case-insensitive filesystem')
    spellings: dict[str, str] = {}
    for name in names:
        for parent in PurePosixPath(name).parents:
            if parent == PurePosixPath('.'):
                continue
            spelling = parent.as_posix()
            identity = path_identity(spelling)
            require_bundle(identity not in identities, f'Archive path is both a file and directory: {spelling}')
            require_bundle(
                spellings.setdefault(identity, spelling) == spelling, f'Archive directory collision: {spelling}'
            )


def read_file_index(value: object, *namespaces: str) -> dict[str, FileEntry]:
    if not isinstance(value, dict):
        raise BundleError('Bundle files must be an object')
    entries: dict[str, FileEntry] = {}
    for file_name, file_record in value.items():
        name = path_name(file_name)
        require_bundle(
            name.startswith(tuple(namespace + '/' for namespace in namespaces)),
            f'File lies outside its allowed directory: {name}',
        )
        entry = record(file_record, {'sha256', 'executable'}, name)
        checksum = text_value(entry['sha256'], 'sha256')
        executable = entry['executable']
        require_bundle(SHA256_HEX_PATTERN.fullmatch(checksum) is not None, f'Invalid SHA256 for {name}')
        if type(executable) is not bool:
            raise BundleError(f'Invalid executable flag for {name}')
        entries[name] = {'sha256': checksum, 'executable': executable}
    check_unique_paths(entries)
    return entries


def read_checks(value: object) -> list[Check]:
    checks: list[Check] = []
    for item in list_value(value, 'checks'):
        check = record(item, {'id', 'instructions'}, 'check')
        checks.append(
            {
                'id': identifier(check['id'], 'check ID'),
                'instructions': text_value(check['instructions'], 'check instructions'),
            }
        )
    require_bundle(
        bool(checks) and len({check['id'] for check in checks}) == len(checks), 'Checks require distinct IDs'
    )
    return checks


def output_names(value: object) -> list[str]:
    names = [path_name(name) for name in list_value(value, 'outputs')]
    require_bundle(all(name.startswith('outputs/') for name in names), 'Declared outputs must be below outputs/')
    require_bundle(len(set(names)) == len(names), 'Declared output paths must be distinct')
    check_unique_paths(dict.fromkeys(names))
    return names


def read_task(value: object) -> Task:
    task = record(
        value,
        {
            'version',
            'task_id',
            'attempt',
            'submission_id',
            'assignment_id',
            'revision',
            'source',
            'capabilities',
            'checks',
            'outputs',
            'files',
        },
        'task manifest',
    )
    require_bundle(
        type(task['version']) is int and task['version'] == BUNDLE_VERSION, 'Unsupported task bundle version'
    )
    source = record(task['source'], {'commit', 'tree'}, 'pinned source')
    return {
        'version': BUNDLE_VERSION,
        'task_id': identifier(task['task_id'], 'task ID'),
        'attempt': attempt_number(task['attempt']),
        'submission_id': identifier(task['submission_id'], 'submission ID'),
        'assignment_id': identifier(task['assignment_id'], 'assignment ID'),
        'revision': text_value(task['revision'], 'workspace revision'),
        'source': {'commit': text_value(source['commit'], 'commit'), 'tree': text_value(source['tree'], 'tree')},
        'capabilities': [text_value(item, 'capability') for item in list_value(task['capabilities'], 'capabilities')],
        'checks': read_checks(task['checks']),
        'outputs': output_names(task['outputs']),
        'files': read_file_index(task['files'], 'inputs'),
    }


def read_task_specification(path: Path) -> TaskSpecification:
    from grading_core import digest

    specification = record(
        parse_json(path.read_bytes()),
        {'task_id', 'attempt', 'submission_id', 'checks'},
        'task specification',
        frozenset({'capabilities', 'files', 'include_snapshot', 'outputs'}),
    )
    include_snapshot = specification.get('include_snapshot', False)
    if type(include_snapshot) is not bool:
        raise BundleError('include_snapshot must be true or false')
    return TaskSpecification(
        task_id=identifier(specification['task_id'], 'task ID'),
        attempt=attempt_number(specification['attempt']),
        submission_id=identifier(specification['submission_id'], 'submission ID'),
        capabilities=tuple(
            text_value(item, 'capability') for item in list_value(specification.get('capabilities', []), 'capabilities')
        ),
        requested_files=tuple(path_name(value) for value in list_value(specification.get('files', []), 'files')),
        include_snapshot=include_snapshot,
        checks=tuple(read_checks(specification['checks'])),
        outputs=tuple(output_names(specification.get('outputs', []))),
        fingerprint=digest(specification),
    )


def write_archive(destination: Path, manifest_name: str, manifest: object, files: Mapping[str, Path]) -> None:
    manifest_bytes = json_bytes(manifest)
    require_bundle(len(manifest_bytes) <= MAX_MANIFEST_BYTES, 'Bundle manifest is too large; split the task')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        archive_path = Path(temporary) / 'archive.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            archive.writestr(manifest_name, manifest_bytes)
            for name, path in sorted(files.items()):
                archive.write(path, name)
        try:
            os.link(archive_path, destination)
        except FileExistsError as error:
            raise BundleError(f'Output already exists: {destination}') from error


def archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for member in archive.infolist():
        name = path_name(member.filename)
        require_bundle(
            name == member.orig_filename and name not in members, f'Duplicate or invalid archive entry: {name}'
        )
        mode = stat.S_IFMT(member.external_attr >> 16)
        require_bundle(
            mode in (0, stat.S_IFREG) and not member.is_dir(), f'Archive entry is not a regular file: {name}'
        )
        members[name] = member
    check_unique_paths(members)
    return members


def archive_manifest(archive: zipfile.ZipFile, members: Mapping[str, zipfile.ZipInfo], name: str) -> object:
    entry = members.get(name)
    if entry is None:
        raise BundleError(f'Archive does not contain {name}')
    require_bundle(entry.file_size <= MAX_MANIFEST_BYTES, 'Bundle manifest is too large')
    return parse_json(archive.read(entry))


def extract_payload(
    archive: zipfile.ZipFile,
    members: Mapping[str, zipfile.ZipInfo],
    root: Path,
    manifest_name: str,
    files: Mapping[str, FileEntry],
) -> None:
    require_bundle(set(members) == {manifest_name, *files}, 'Archive contents do not match the declared files')
    require_bundle(
        sum(members[name].file_size for name in files) <= shutil.disk_usage(root).free,
        'Not enough free space to extract the bundle',
    )
    for name, entry in files.items():
        target = root.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(members[name]) as stream, target.open('xb') as output:
            shutil.copyfileobj(stream, output)
        require_bundle(file_checksum(target) == entry['sha256'], f'Checksum mismatch: {name}')
        if HAS_UNIX_FILE_MODES:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if entry['executable'] else 0))


def task_directory(root: Path, task_id: str, attempt: int) -> Path:
    root = root.expanduser().resolve()
    parts = ('remote_tasks', task_id, str(attempt))
    require_bundle(
        all(not is_linked_path(root.joinpath(*parts[:length])) for length in range(1, len(parts) + 1)),
        'Remote task directories must not be symlinks',
    )
    return root.joinpath(*parts)


def issue_task(directory: Path, task: Task) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / TASK_NAME
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        staged = Path(temporary) / TASK_NAME
        staged.write_bytes(json_bytes(task))
        try:
            os.link(staged, target)
        except FileExistsError:
            require_bundle(
                parse_json(regular_file(directory, TASK_NAME).read_bytes()) == task,
                'This task attempt already has different inputs. Use a new attempt number.',
            )


def snapshot_files(root: Path, snapshot_prefix: str) -> dict[str, Path]:
    snapshot_root = root / snapshot_prefix
    files_in_scope: dict[str, Path] = {}
    for parent_name, directories, file_names in os.walk(snapshot_root):
        directories[:] = [name for name in directories if name != '.git']
        parent = Path(parent_name)
        for name in directories:
            directory = parent / name
            require_bundle(
                not is_linked_path(directory), f'Snapshot contains a symlink: {directory.relative_to(snapshot_root)}'
            )
        for name in file_names:
            relative = (parent / name).relative_to(root).as_posix()
            files_in_scope[f'inputs/{relative}'] = regular_file(root, relative)
    return files_in_scope


def pack_bundle(root: Path, specification: Path, destination: Path) -> Path:
    from grading_core import Workspace, digest
    from grading_sources import inspect_snapshots

    task_specification = read_task_specification(specification)
    submission_id = task_specification.submission_id
    workspace = Workspace(root)
    manifest = workspace.prepared()
    require_bundle(submission_id in manifest['submissions'], f'Unknown submission: {submission_id}')
    source = manifest['submissions'][submission_id]
    snapshot_prefix = f'snapshots/{submission_id}'
    selected: dict[str, Path] = {}
    for name in task_specification.requested_files:
        require_bundle(
            name.startswith((f'{snapshot_prefix}/', 'input/'))
            and '.git' not in PurePosixPath(name).parts
            and name != 'input/roster.csv',
            f'File lies outside task scope: {name}',
        )
        selected[f'inputs/{name}'] = regular_file(root, name)
    if task_specification.include_snapshot or any(name.startswith(f'inputs/{snapshot_prefix}/') for name in selected):
        inspect_snapshots(workspace, {'submissions': {submission_id: source}})
    if task_specification.include_snapshot:
        selected.update(snapshot_files(root, snapshot_prefix))
    task: Task = {
        'version': BUNDLE_VERSION,
        'task_id': task_specification.task_id,
        'attempt': task_specification.attempt,
        'submission_id': submission_id,
        'assignment_id': workspace.config['assignment_id'],
        'revision': digest(manifest),
        'source': {'commit': source['commit'], 'tree': source['tree']},
        'capabilities': list(task_specification.capabilities),
        'checks': list(task_specification.checks),
        'outputs': list(task_specification.outputs),
        'files': file_index(selected),
    }
    require_bundle(not destination.exists(), f'Output already exists: {destination}')
    issue_task(task_directory(root, task_specification.task_id, task_specification.attempt), task)
    write_archive(destination, TASK_NAME, task, selected)
    return destination


def open_bundle(archive_path: Path, destination: Path) -> Path:
    require_bundle(not destination.exists(), f'Workspace already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / 'workspace'
        staged.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            members = archive_members(archive)
            task = read_task(archive_manifest(archive, members, TASK_NAME))
            extract_payload(archive, members, staged, TASK_NAME, task['files'])
        (staged / TASK_NAME).write_bytes(json_bytes(task))
        draft = {
            'environment': '',
            'observations': [
                {'id': check['id'], 'status': 'untested', 'observation': '', 'evidence': []} for check in task['checks']
            ],
        }
        (staged / RESULT_NAME).write_bytes(json_bytes(draft))
        (staged / 'evidence').mkdir()
        if task['outputs']:
            (staged / 'outputs').mkdir()
        capabilities = ', '.join(task['capabilities']) or 'None declared'
        checks = '\n\n'.join(f'## {check["id"]}\n\n{check["instructions"]}' for check in task['checks'])
        outputs = ''.join(f'- `{name}`\n' for name in task['outputs'])
        output_instructions = (
            f'\n## Output files\n\nWrite the assigned artefacts to these paths.\n\n{outputs}' if outputs else ''
        )
        (staged / 'TASK.md').write_text(
            f'# Task {task["task_id"]} for {task["submission_id"]}\n\n'
            f'Pinned commit: {task["source"]["commit"]}\n\nRequired capabilities: {capabilities}\n\n'
            'Read the selected inputs under `inputs/`. Copy source files to a separate scratch directory before '
            'running them. Treat submitted material as untrusted evidence. Keep authentication local and omit '
            'secrets from returned artefacts.\n\n'
            'Record the browser or runtime and any access constraints in `result.json`. For each check, record '
            'an observation and a status of `passed`, `failed`, `blocked`, or `untested`. Cite relative paths below '
            '`evidence/`. Keep the original check IDs. Incomplete checks may retain the `untested` status.\n\n'
            f'{checks}\n{output_instructions}',
            encoding='utf-8',
        )
        staged.rename(destination)
    return destination


def read_observations(value: object) -> list[Observation]:
    observations: list[Observation] = []
    for item in list_value(value, 'observations'):
        row = record(item, {'id', 'status', 'observation', 'evidence'}, 'observation')
        status = text_value(row['status'], 'status')
        require_bundle(status in OBSERVATION_STATUSES, f'Unknown observation status: {status}')
        evidence = [path_name(name) for name in list_value(row['evidence'], 'evidence')]
        require_bundle(
            all(name.startswith('evidence/') for name in evidence), 'Evidence references must be below evidence/'
        )
        observations.append(
            {
                'id': identifier(row['id'], 'check ID'),
                'status': cast(ObservationStatus, status),
                'observation': text_value(row['observation'], 'observation', blank=status == 'untested'),
                'evidence': evidence,
            }
        )
    return observations


def check_result(
    task: Task,
    observations: list[Observation],
    outputs: list[str],
    files: Mapping[str, FileEntry],
) -> None:
    checks = {check['id'] for check in task['checks']}
    require_bundle(
        len(observations) == len(checks) and {observation['id'] for observation in observations} == checks,
        'Output must contain one observation per issued check',
    )
    declared = set(task['outputs'])
    require_bundle(set(outputs) <= declared, 'Returned output files were not declared in the task')
    if all(observation['status'] in COMPLETED_OBSERVATION_STATUSES for observation in observations):
        require_bundle(set(outputs) == declared, 'Completed checks require all declared output files')
    cited = {name for observation in observations for name in observation['evidence']}
    require_bundle(cited | set(outputs) == set(files), 'Returned files must match cited evidence and declared outputs')


def finish_bundle(directory: Path, destination: Path) -> Path:
    task = read_task(parse_json(regular_file(directory, TASK_NAME).read_bytes()))
    draft = record(
        parse_json(regular_file(directory, RESULT_NAME).read_bytes()), {'environment', 'observations'}, 'result draft'
    )
    observations = read_observations(draft['observations'])
    outputs = [name for name in task['outputs'] if (directory / name).exists() or is_linked_path(directory / name)]
    cited = {name for observation in observations for name in observation['evidence']}
    selected = {name: regular_file(directory, name) for name in cited | set(outputs)}
    entries = file_index(selected)
    check_result(task, observations, outputs, entries)
    input_files = {name: regular_file(directory, name) for name in task['files']}
    require_bundle(files_match(task['files'], file_index(input_files)), 'The original task inputs have changed')
    result: Result = {
        'version': BUNDLE_VERSION,
        'task_id': task['task_id'],
        'attempt': task['attempt'],
        'revision': task['revision'],
        'task_digest': json_digest(task),
        'environment': text_value(draft['environment'], 'environment'),
        'observations': observations,
        'outputs': outputs,
        'files': entries,
    }
    write_archive(destination, RESULT_NAME, result, selected)
    return destination


def read_result(value: object) -> Result:
    result = record(
        value,
        {
            'version',
            'task_id',
            'attempt',
            'revision',
            'task_digest',
            'environment',
            'observations',
            'outputs',
            'files',
        },
        'returned result',
    )
    require_bundle(
        type(result['version']) is int and result['version'] == BUNDLE_VERSION, 'Unsupported result bundle version'
    )
    return {
        'version': BUNDLE_VERSION,
        'task_id': identifier(result['task_id'], 'task ID'),
        'attempt': attempt_number(result['attempt']),
        'revision': text_value(result['revision'], 'workspace revision'),
        'task_digest': text_value(result['task_digest'], 'task digest'),
        'environment': text_value(result['environment'], 'environment'),
        'observations': read_observations(result['observations']),
        'outputs': output_names(result['outputs']),
        'files': read_file_index(result['files'], 'evidence', 'outputs'),
    }


def validate_return(root: Path, directory: Path, result: Result) -> ImportReceipt:
    from grading_core import Workspace, digest

    require_bundle((directory / TASK_NAME).is_file(), 'No matching task was issued in this workspace')
    task = read_task(parse_json(regular_file(directory, TASK_NAME).read_bytes()))
    require_bundle(
        (result['task_id'], result['attempt']) == (task['task_id'], task['attempt']), 'Result task identity differs'
    )
    require_bundle(
        result['revision'] == task['revision'] == digest(Workspace(root).prepared()),
        'Result is for a stale workspace revision. Retain it separately for review.',
    )
    require_bundle(result['task_digest'] == json_digest(task), 'Result does not match the issued task inputs')
    check_result(task, result['observations'], result['outputs'], result['files'])
    return {'task_digest': json_digest(task), 'result_digest': json_digest(result)}


def imported_result(root: Path, task_id: str, attempt: int) -> Result | None:
    directory = task_directory(root, identifier(task_id, 'task ID'), attempt_number(attempt))
    destination = directory / 'result'
    require_bundle(not is_linked_path(destination), 'Result directory must not be a symlink')
    if not destination.exists():
        return None
    result = read_result(parse_json(regular_file(destination, RESULT_NAME).read_bytes()))
    require_bundle((result['task_id'], result['attempt']) == (task_id, attempt), 'Result task identity differs')
    receipt = parse_json(regular_file(destination, 'receipt.json').read_bytes())
    require_bundle(receipt == validate_return(root, directory, result), 'Imported result does not match its receipt')
    require_bundle(
        files_match(result['files'], file_index({name: regular_file(destination, name) for name in result['files']})),
        'Imported evidence no longer matches its receipt',
    )
    return result


def import_bundle(root: Path, archive_path: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = archive_members(archive)
        result = read_result(archive_manifest(archive, members, RESULT_NAME))
        task_id, attempt = result['task_id'], result['attempt']
        directory = task_directory(root, task_id, attempt)
        receipt = validate_return(root, directory, result)
        destination = directory / 'result'
        require_bundle(not is_linked_path(destination), 'Result directory must not be a symlink')
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            staged = Path(temporary) / 'result'
            staged.mkdir()
            extract_payload(archive, members, staged, RESULT_NAME, result['files'])
            (staged / RESULT_NAME).write_bytes(json_bytes(result))
            (staged / 'receipt.json').write_bytes(json_bytes(receipt))
            try:
                staged.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                require_bundle(
                    imported_result(root, task_id, attempt) == result,
                    'A different result was already imported for this task attempt',
                )
    return destination / 'receipt.json'


def configure_bundle_parser(parser: argparse.ArgumentParser, *, detached: bool = False) -> None:
    commands = parser.add_subparsers(dest='bundle_command', required=True)
    opening = commands.add_parser('open', help='Open a task archive as a standalone scratch workspace')
    opening.add_argument('archive', type=Path)
    opening.add_argument('destination', type=Path)
    finishing = commands.add_parser('finish', help='Package the recorded observations and cited evidence')
    finishing.add_argument('directory', type=Path)
    finishing.add_argument('destination', type=Path)
    if detached:
        return
    packing = commands.add_parser('pack', help='Issue a scoped task archive from a prepared workspace')
    packing.add_argument('specification', type=Path)
    packing.add_argument('destination', type=Path)
    importing = commands.add_parser('import', help='Validate and import a returned task result')
    importing.add_argument('archive', type=Path)


def run_bundle_command(arguments: argparse.Namespace, root: Path) -> Path:
    try:
        if arguments.bundle_command == 'open':
            return open_bundle(arguments.archive, arguments.destination)
        if arguments.bundle_command == 'finish':
            return finish_bundle(arguments.directory, arguments.destination)
        if arguments.bundle_command == 'pack':
            return pack_bundle(root, arguments.specification, arguments.destination)
        return import_bundle(root, arguments.archive)
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError) as error:
        raise BundleError(str(error)) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    configure_bundle_parser(parser, detached=True)
    try:
        print(run_bundle_command(parser.parse_args(), Path.cwd()))
    except BundleError as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
