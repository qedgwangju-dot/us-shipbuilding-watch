"""Persist a Telegram send intent to existing repository state before transmitting.

No new credentials or service. Production uses the checkout's existing origin auth.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

EXPECTED_REPOSITORY = 'qedgwangju-dot/us-shipbuilding-watch'
EXPECTED_ORIGIN = 'https://github.com/' + EXPECTED_REPOSITORY
STATE_NAMES = ('refinery_watch_state.json', 'spr_venezuela_watch_state.json', 'energy_delivery_state.json')


class DurableCheckpointError(RuntimeError):
    pass


def _git(root, *args, check=True):
    try:
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise DurableCheckpointError('Git checkpoint command timed out: ' + args[0]) from None
    if check and result.returncode:
        # Git transport output can contain authentication URLs; never echo it.
        raise DurableCheckpointError('Git checkpoint command failed: ' + args[0])
    return result


def _origin_key(value):
    value = value.strip().rstrip('/')
    return value[:-4] if value.endswith('.git') else value


def _validate_checkpoint(root, marker_path, expected_remote):
    if os.getenv('GITHUB_REPOSITORY') != EXPECTED_REPOSITORY:
        raise DurableCheckpointError('Unexpected repository context')
    if os.getenv('GITHUB_REF') != 'refs/heads/main' or os.getenv('GITHUB_EVENT_NAME') == 'pull_request':
        raise DurableCheckpointError('Durable sends are allowed only from production main')
    if not os.getenv('GITHUB_RUN_ID'):
        raise DurableCheckpointError('Missing workflow run identity')
    root = Path(root).resolve()
    if Path(_git(root, 'rev-parse', '--show-toplevel').stdout.decode().strip()).resolve() != root:
        raise DurableCheckpointError('Unexpected checkpoint worktree')
    if not os.getenv('GITHUB_WORKSPACE') or Path(os.environ['GITHUB_WORKSPACE']).resolve() != root:
        raise DurableCheckpointError('Unexpected workflow worktree')
    if _git(root, 'branch', '--show-current').stdout.decode().strip() != 'main':
        raise DurableCheckpointError('Worktree is not on main')
    for direction in ((), ('--push',)):
        origins = _git(root, 'remote', 'get-url', *direction, '--all', 'origin').stdout.decode().splitlines()
        if not origins or any(_origin_key(value) != _origin_key(expected_remote) for value in origins):
            raise DurableCheckpointError('Unexpected checkpoint remote')
    snapshots = {name: (root / name).read_bytes() for name in STATE_NAMES}
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in snapshots.items()}
    marker = json.loads(Path(marker_path).read_text())
    if (marker.get('persistence_blocked') or marker.get('run_id') != os.environ['GITHUB_RUN_ID']
            or marker.get('state_sha256') != digests or marker.get('schema_version') != 1):
        raise DurableCheckpointError('Checkpoint identity/hash mismatch or blocked persistence')
    if json.loads(snapshots['energy_delivery_state.json']).get('schema_version') != 1:
        raise DurableCheckpointError('Unexpected delivery state schema')
    return root, snapshots


def _persist_snapshot(root, snapshots, commit_message):
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()}
    for args in [('diff', '--name-only', '-z'), ('diff', '--cached', '--name-only', '-z')]:
        paths = _git(root, *args).stdout.decode().split('\0')
        if any(path and path not in STATE_NAMES for path in paths):
            raise DurableCheckpointError('Refusing unrelated worktree or staged changes')

    # Reject unpublished code commits too, not just currently staged paths.
    _git(root, 'fetch', 'origin', 'main')
    if _git(root, 'rev-list', '--merges', 'FETCH_HEAD..HEAD').stdout.strip():
        raise DurableCheckpointError('Refusing unpublished merge commits')
    unpublished = _git(root, 'log', '-m', '--format=', '--name-only', 'FETCH_HEAD..HEAD').stdout.decode().splitlines()
    if any(path and path not in STATE_NAMES for path in unpublished):
        raise DurableCheckpointError('Refusing unrelated unpublished commits')

    _git(root, 'config', 'user.name', 'github-actions[bot]')
    _git(root, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    _git(root, 'add', '--', *STATE_NAMES)
    if _git(root, 'diff', '--cached', '--quiet', check=False).returncode:
        _git(root, 'commit', '-m', commit_message)

    for _ in range(3):
        _git(root, 'fetch', 'origin', 'main')
        remote_commit = _git(root, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        common = _git(root, 'merge-base', 'HEAD', remote_commit).stdout.decode().strip()
        changed = _git(root, 'diff', '--name-only', common, remote_commit, '--', *STATE_NAMES).stdout
        if changed:
            # Even a clean, nonconflicting state merge could invalidate our
            # in-memory snapshot. Never rebase over another writer's state.
            raise DurableCheckpointError('Remote state changed; no Telegram attempt authorized')
        rebase = _git(root, 'rebase', remote_commit, check=False)
        if rebase.returncode:
            _git(root, 'rebase', '--abort', check=False)
            raise DurableCheckpointError('Remote state conflict; no Telegram attempt authorized')
        if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != digest for name, digest in digests.items()):
            raise DurableCheckpointError('State changed during rebase; no send authorized')
        push = _git(root, 'push', 'origin', 'HEAD:refs/heads/main', check=False)
        fetch = _git(root, 'fetch', 'origin', 'main', check=False)
        if fetch.returncode:
            raise DurableCheckpointError('Remote checkpoint readback unavailable; no send authorized')
        remote_states = {name: _git(root, 'show', 'FETCH_HEAD:' + name, check=False) for name in STATE_NAMES}
        if all(value.returncode == 0 and hashlib.sha256(value.stdout).hexdigest() == digests[name] for name, value in remote_states.items()):
            commit = _git(root, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
            if len(commit) == 40 and all(c in '0123456789abcdef' for c in commit):
                return commit
        if push.returncode == 0:
            # A later writer changed the state before readback. Do not overwrite it.
            raise DurableCheckpointError('Published intent is no longer current; no send authorized')
        # A rejected/uncertain push is only retried after reading the remote. A
        # same-state conflict will fail the next rebase instead of overwriting it.
    raise DurableCheckpointError('Remote checkpoint was not verified; no send authorized')


def persist(root, marker_path, *, intent=None, expected_remote=EXPECTED_ORIGIN):
    """Publish and read back the exact three-file bundle; never force or merge state."""
    root, snapshots = _validate_checkpoint(root, marker_path, expected_remote)
    if intent is not None:
        key, index, message_digest, destination_digest = intent
        entry = json.loads(snapshots['energy_delivery_state.json'])['outbox'].get(key, {})
        chunks = entry.get('chunks', [])
        if (entry.get('status') != 'delivery_unknown' or not 0 <= index < len(chunks)
                or chunks[index].get('status') != 'delivery_unknown'
                or chunks[index].get('message_sha256') != message_digest
                or hashlib.sha256(chunks[index].get('text', '').encode()).hexdigest() != message_digest
                or entry.get('destination_sha256') != destination_digest):
            raise DurableCheckpointError('Exact prepared delivery intent is missing')
    return _persist_snapshot(root, snapshots, 'Checkpoint refinery and SPR delivery states [skip ci]')
