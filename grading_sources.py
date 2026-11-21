"""Pin submission sources without modifying them or running student code."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile, ZipInfo

from grading_core import (
    GradingError,
    Snapshot,
    SnapshotCollection,
    SnapshotPolicy,
    SourceManifest,
    Submission,
    Workspace,
    digest,
    file_digest,
    inputs_digest,
    nonempty,
    require,
    write_json,
    write_text,
)


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
GIT_TIMEOUT_SECONDS = 120
ARCHIVE_MODE_SHIFT_BITS = 16
FIRST_PRINTABLE_ASCII_CODE = 32
GIT_SUBMODULE_MODE = '160000 '
SOURCE_MANIFEST_VERSION = 2
WINDOWS_RESERVED_STEM = re.compile(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])', re.IGNORECASE)
ARCHIVE_FORBIDDEN_FILENAME_CHARACTERS = frozenset('<>:"\\|?*')
SUPPORTS_EXECUTABLE_MODE = os.name != 'nt'

LocalInventory = dict[str, tuple[str, bool]]


def git(*arguments: str | Path) -> str:
    return subprocess.check_output(
        [
            'git',
            '-c',
            f'core.hooksPath={os.devnull}',
            '-c',
            'core.autocrlf=false',
            '-c',
            'i18n.logOutputEncoding=UTF-8',
            *map(str, arguments),
        ],
        encoding='utf-8',
        errors='replace',
        stderr=subprocess.PIPE,
        timeout=GIT_TIMEOUT_SECONDS,
    ).strip()


def inspect_snapshots(workspace: Workspace, manifest: SnapshotCollection) -> None:
    for submission_id, snapshot in manifest['submissions'].items():
        repository = workspace.root / 'snapshots' / submission_id
        if snapshot.get('kind') == 'local':
            fingerprint = inspect_local_fingerprint(repository, snapshot)
            require(
                fingerprint == snapshot['tree'] == snapshot['commit'],
                f'{submission_id}: snapshot modified. Run probes in scratch copies.',
            )
            continue
        require(
            git('-C', repository, 'rev-parse', 'HEAD') == snapshot['commit'], f'{submission_id}: snapshot HEAD changed'
        )
        changes = git('-C', repository, 'status', '--porcelain', '--untracked-files=all', '--ignored')
        require(not changes, f'{submission_id}: snapshot modified. Run probes in scratch copies.')
        require(
            git('-C', repository, 'rev-parse', 'HEAD^{tree}') == snapshot['tree'], f'{submission_id}: tree mismatch'
        )
        git('-C', repository, 'merge-base', '--is-ancestor', snapshot['commit'], snapshot['branch_head'])


def local_inventory(source: Path) -> LocalInventory:
    require(source.is_dir(), f'Submission directory is missing: {source}')
    inventory: LocalInventory = {}
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        require(
            '.git' not in relative.parts and not path.is_symlink(),
            f'Local submissions require ordinary files without Git metadata: {relative}',
        )
        if path.is_dir():
            continue
        require(path.is_file(), f'Unsupported local submission file: {relative}')
        inventory[relative.as_posix()] = (file_digest(path), bool(path.stat().st_mode & stat.S_IXUSR))
    require(inventory, f'Local submission is empty: {source}')
    return inventory


def mode_aware_inventory(inventory: LocalInventory, executable_files: list[str] | None) -> LocalInventory:
    executable_paths = set(executable_files or ())
    return {path: (checksum, path in executable_paths) for path, (checksum, _) in inventory.items()}


def inspect_local_fingerprint(repository: Path, snapshot: Snapshot) -> str:
    inventory = local_inventory(repository)
    if 'executable_files' not in snapshot:
        return digest(inventory)
    executable_files = snapshot['executable_files']
    recorded_inventory = mode_aware_inventory(inventory, executable_files)
    if SUPPORTS_EXECUTABLE_MODE and executable_files is not None:
        require(inventory == recorded_inventory, f'{repository.name}: snapshot modified. Run probes in scratch copies.')
    return digest(recorded_inventory)


def is_portable_archive_part(part: str) -> bool:
    if part.lower() == '.git' or part.endswith(('.', ' ')):
        return False
    return (
        not ARCHIVE_FORBIDDEN_FILENAME_CHARACTERS.intersection(part)
        and not any(ord(character) < FIRST_PRINTABLE_ASCII_CODE for character in part)
        and not WINDOWS_RESERVED_STEM.fullmatch(part.partition('.')[0].rstrip(' '))
    )


def archive_member(entry: ZipInfo) -> tuple[PurePosixPath, int]:
    name = entry.orig_filename.rstrip('/')
    relative = PurePosixPath(name)
    permissions = entry.external_attr >> ARCHIVE_MODE_SHIFT_BITS
    expected_kind = stat.S_IFDIR if entry.is_dir() else stat.S_IFREG
    require(
        name
        and name != '.'
        and name == relative.as_posix()
        and not relative.is_absolute()
        and all(is_portable_archive_part(part) for part in relative.parts)
        and stat.S_IFMT(permissions) in (0, expected_kind),
        f'Unsupported submission ZIP member: {entry.filename}',
    )
    return relative, permissions


def extract_submission_zip(source: Path, destination: Path) -> list[str]:
    executable_files: list[str] = []
    with ZipFile(source) as archive:
        entries = archive.infolist()
        require(
            len(entries) <= MAX_ARCHIVE_ENTRIES and sum(entry.file_size for entry in entries) <= MAX_ARCHIVE_BYTES,
            f'Submission ZIP exceeds extraction limits: {source}',
        )
        seen: set[str] = set()
        case_insensitive_paths: dict[str, str] = {}
        for entry in entries:
            relative, permissions = archive_member(entry)
            name = relative.as_posix()
            require(name not in seen, f'Duplicate submission ZIP member: {name}')
            seen.add(name)
            for component in (relative, *relative.parents):
                component_name = component.as_posix()
                original = case_insensitive_paths.setdefault(
                    unicodedata.normalize('NFC', component_name).casefold(), component_name
                )
                require(original == component_name, f'Conflicting submission ZIP member: {name}')
            executable = not entry.is_dir() and bool(permissions & stat.S_IXUSR)
            write_archive_member(archive, entry, destination / relative, executable)
            if executable:
                executable_files.append(name)
    return sorted(executable_files)


def write_archive_member(archive: ZipFile, entry: ZipInfo, target: Path, executable: bool) -> None:
    if entry.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(entry) as contents, target.open('xb') as output:
        shutil.copyfileobj(contents, output)
    if SUPPORTS_EXECUTABLE_MODE and executable:
        target.chmod(target.stat().st_mode | stat.S_IXUSR)


def stage_local_submission(source: Path, staging: Path, submission_id: str) -> list[str] | None:
    if source.is_dir():
        inventory = local_inventory(source)
        shutil.copytree(source, staging, symlinks=True)
        return [path for path, (_, executable) in inventory.items() if executable] if SUPPORTS_EXECUTABLE_MODE else None
    staging.mkdir()
    try:
        return extract_submission_zip(source, staging)
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        raise GradingError(f'{submission_id}: invalid submission ZIP: {source}') from error


def pin_local_submission(root: Path, submission_id: str, submission: Submission) -> Snapshot:
    source = (root / Path(submission['repo_url']).expanduser()).resolve()
    target = root / 'snapshots' / submission_id
    require(
        not target.is_relative_to(source), f'{submission_id}: submission source contains its own snapshot destination'
    )
    require(
        source.is_dir() or (source.is_file() and source.suffix.lower() == '.zip'),
        f'{submission_id}: expected a local directory or ZIP when branch is blank',
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as temporary:
        staging = Path(temporary) / 'submission'
        executable_files = stage_local_submission(source, staging, submission_id)
        inventory = mode_aware_inventory(local_inventory(staging), executable_files)
        if target.exists():
            previous_snapshot = local_inventory(target)
            require(
                (
                    previous_snapshot
                    if SUPPORTS_EXECUTABLE_MODE
                    else mode_aware_inventory(previous_snapshot, executable_files)
                )
                == inventory,
                f'{submission_id}: partial preparation has a different snapshot. Start a new workspace.',
            )
        else:
            staging.replace(target)
    fingerprint = digest(inventory)
    evidence = root / 'evidence' / 'source' / submission_id
    write_json(evidence / 'files.json', inventory)
    return {
        'kind': 'local',
        'commit': fingerprint,
        'tree': fingerprint,
        'repo_url': submission['repo_url'],
        'branch': '',
        'branch_head': fingerprint,
        'author_time': '',
        'committer_time': '',
        'excluded_commits': [],
        'members': submission['members'],
        'executable_files': executable_files,
    }


def copy_inherited_snapshot(
    root: Path,
    submission_id: str,
    submission: Submission,
    source: Path,
    snapshot: Snapshot,
) -> Snapshot:
    target = root / 'snapshots' / submission_id
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=True)
    origin_evidence = source.parent.parent / 'evidence' / 'source' / submission_id
    if origin_evidence.is_dir():
        shutil.copytree(origin_evidence, root / 'evidence' / 'source' / submission_id, dirs_exist_ok=True)
    return {**snapshot, 'repo_url': submission['repo_url'], 'members': submission['members']}


def cached_repository(root: Path, submission_id: str, source: str, branch: str) -> Path:
    require(not branch.startswith('-'), f'{submission_id}: invalid branch')
    git('check-ref-format', '--branch', branch)
    cache = root / 'repositories' / f'{submission_id}.git'
    local_source = Path(source).expanduser()
    origin = (root / local_source).resolve().as_posix() if local_source.is_absolute() or ':' not in source else source
    if cache.exists():
        require(
            git('--git-dir', cache, 'remote', 'get-url', 'origin') == origin, f'{submission_id}: cached origin differs'
        )
    else:
        cache.parent.mkdir(exist_ok=True)
        git('clone', '--bare', '--no-local', '--', origin, cache)
    git('--git-dir', cache, 'fetch', '--no-tags', 'origin', f'+refs/heads/{branch}:refs/heads/{branch}')
    return cache


def choose_git_commit(
    cache: Path,
    submission_id: str,
    submission: Submission,
    branch_head: str,
    policy: SnapshotPolicy,
    replacement_commit: str | None,
) -> tuple[str, list[str]]:
    if replacement_commit is not None:
        return git('--git-dir', cache, 'rev-parse', replacement_commit + '^{commit}'), []
    if policy['mode'] == 'explicit':
        require(submission['commit'], f'{submission_id}: explicit mode requires a submitted commit')
        return git('--git-dir', cache, 'rev-parse', submission['commit'] + '^{commit}'), []
    require(not submission['commit'], f'{submission_id}: commit column is only used in explicit mode')
    if policy['mode'] == 'head':
        return branch_head, []
    if policy['deadline'] is None:
        raise GradingError('Deadline source selection requires a cutoff')
    cutoff_seconds = datetime.fromisoformat(policy['deadline']).timestamp()
    eligible: list[str] = []
    excluded: list[str] = []
    history = git('--git-dir', cache, 'log', '--first-parent', '--format=%H %at %ct', branch_head)
    for entry in history.splitlines():
        commit, author, committer = entry.split()
        commit_seconds = int(committer)
        source_seconds = max(int(author), commit_seconds) if policy['timestamp'] == 'both' else commit_seconds
        qualifies = (
            source_seconds < cutoff_seconds if policy['boundary'] == 'exclusive' else source_seconds <= cutoff_seconds
        )
        (eligible if qualifies else excluded).append(commit)
    require(eligible, f'{submission_id}: no eligible commit before cutoff. Record a course ruling before retrying.')
    return eligible[0], excluded


def pin_git_submission(
    root: Path,
    submission_id: str,
    submission: Submission,
    policy: SnapshotPolicy,
    replacement_commit: str | None,
) -> Snapshot:
    branch = submission['branch']
    cache = cached_repository(root, submission_id, submission['repo_url'], branch)
    branch_head = git('--git-dir', cache, 'rev-parse', f'refs/heads/{branch}')
    commit, excluded = choose_git_commit(cache, submission_id, submission, branch_head, policy, replacement_commit)
    git('--git-dir', cache, 'merge-base', '--is-ancestor', commit, branch_head)
    tree = git('--git-dir', cache, 'rev-parse', commit + '^{tree}')
    inventory = git('--git-dir', cache, 'ls-tree', '-r', commit)
    require(
        not any(entry.startswith(GIT_SUBMODULE_MODE) for entry in inventory.splitlines()),
        f'{submission_id}: submodules need an explicit materialisation procedure',
    )
    author_time, committer_time = git('--git-dir', cache, 'show', '-s', '--format=%aI%n%cI', commit).splitlines()
    target = root / 'snapshots' / submission_id
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        git('clone', '--no-local', '--no-checkout', '--', cache, target)
        git('-C', target, 'checkout', '--detach', commit)
    require(
        git('-C', target, 'rev-parse', 'HEAD') == commit,
        f'{submission_id}: partial preparation has a different snapshot. Start a new workspace.',
    )
    evidence = root / 'evidence' / 'source' / submission_id
    write_text(evidence / 'tree.txt', inventory + '\n')
    write_text(evidence / 'history.txt', git('-C', target, 'log', '--format=%H %aI %cI %s', commit) + '\n')
    write_text(evidence / 'excluded-changes.txt', git('-C', target, 'diff', '--stat', commit, branch_head) + '\n')
    return {
        'commit': commit,
        'tree': tree,
        'branch': branch,
        'branch_head': branch_head,
        'author_time': author_time,
        'committer_time': committer_time,
        'excluded_commits': excluded,
        'repo_url': submission['repo_url'],
        'members': submission['members'],
    }


def validate_preparation(workspace: Workspace) -> None:
    require(workspace.config['policy_confirmed'], 'Set policy_confirmed to true only after completing assignment setup')
    workspace.require_scopes()
    inputs = workspace.root / 'input'
    if (inputs / 'verification.json').is_file():
        from grading_coverage import validate_checklist

        validate_checklist(workspace)
    require(workspace.roster, 'Add submissions to input/roster.csv')
    for filename in ('brief.md', 'rubric.md', 'policy.md'):
        nonempty((inputs / filename).read_text(encoding='utf-8'), filename)
    if workspace.config['baseline_required']:
        baseline = inputs / 'baseline'
        has_baseline = any(
            path.is_file() and '.git' not in path.relative_to(baseline).parts and path.name != '.gitkeep'
            for path in baseline.rglob('*')
        )
        require(has_baseline, 'Baseline is required but empty')


def prepare(
    workspace: Workspace,
    inherited_snapshots: Mapping[str, tuple[Path, Snapshot]] | None = None,
    replacement_commits: Mapping[str, str] | None = None,
) -> SourceManifest:
    root = workspace.root
    validate_preparation(workspace)
    if (root / 'manifest.json').exists():
        manifest = workspace.prepared()
        inspect_snapshots(workspace, manifest)
        return manifest
    initial_digest = inputs_digest(root)
    policy = workspace.config['snapshot_policy']
    submissions: dict[str, Snapshot] = {}
    for submission_id, submission in workspace.roster.items():
        inherited = inherited_snapshots.get(submission_id) if inherited_snapshots else None
        replacement = replacement_commits.get(submission_id) if replacement_commits else None
        if inherited is not None:
            submissions[submission_id] = copy_inherited_snapshot(root, submission_id, submission, *inherited)
        elif submission['branch']:
            submissions[submission_id] = pin_git_submission(root, submission_id, submission, policy, replacement)
        else:
            submissions[submission_id] = pin_local_submission(root, submission_id, submission)
    require(initial_digest == inputs_digest(root), 'Inputs changed during preparation')
    manifest = {
        'version': SOURCE_MANIFEST_VERSION,
        'inputs_digest': initial_digest,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'snapshot_policy': policy,
        'submissions': submissions,
    }
    inspect_snapshots(workspace, manifest)
    for author in (*workspace.config['graders'], 'arbiter'):
        (root / 'evaluations' / author).mkdir(parents=True, exist_ok=True)
    for folder in ('corrections', 'verification', 'review', 'scratch', 'reports', 'exports'):
        (root / folder).mkdir(exist_ok=True)
    write_json(root / 'schemas' / 'evaluation.json', workspace.schema('grader'))
    write_json(root / 'schemas' / 'verdict.json', workspace.schema('arbiter'))
    write_json(root / 'manifest.json', manifest)
    return manifest
