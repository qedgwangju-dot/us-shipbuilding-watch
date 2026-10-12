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
STATE_NAME = 'semiconductor_consumption_state.json'


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


def _validate_checkpoint(state_path, marker_path, expected_remote):
    if os.getenv('GITHUB_REPOSITORY') != EXPECTED_REPOSITORY:
        raise DurableCheckpointError('Unexpected repository context')
    if os.getenv('GITHUB_REF') != 'refs/heads/main' or os.getenv('GITHUB_EVENT_NAME') == 'pull_request':
        raise DurableCheckpointError('Durable sends are allowed only from production main')
    if not os.getenv('GITHUB_RUN_ID'):
        raise DurableCheckpointError('Missing workflow run identity')
    state_path = Path(state_path).resolve()
    marker_path = Path(marker_path).resolve()
    root = Path(_git(state_path.parent, 'rev-parse', '--show-toplevel').stdout.decode().strip()).resolve()
    if state_path != root / STATE_NAME:
        raise DurableCheckpointError('Unexpected checkpoint state path')
    workspace = os.getenv('GITHUB_WORKSPACE')
    if not workspace or Path(workspace).resolve() != root:
        raise DurableCheckpointError('Unexpected workflow worktree')
    if _git(root, 'branch', '--show-current').stdout.decode().strip() != 'main':
        raise DurableCheckpointError('Worktree is not on main')
    for direction in ((), ('--push',)):
        origins = _git(root, 'remote', 'get-url', *direction, '--all', 'origin').stdout.decode().splitlines()
        if not origins or any(_origin_key(value) != _origin_key(expected_remote) for value in origins):
            raise DurableCheckpointError('Unexpected checkpoint remote')

    state_bytes = state_path.read_bytes()
    state_digest = hashlib.sha256(state_bytes).hexdigest()
    marker = json.loads(marker_path.read_text())
    if marker.get('persistence_blocked'):
        raise DurableCheckpointError('Durable checkpoint failed; preserve remote state and inspect artifacts')
    if (marker.get('run_id') != os.environ['GITHUB_RUN_ID'] or marker.get('state_sha256') != state_digest
            or marker.get('schema_version') != 2 or marker.get('persistence_blocked')):
        raise DurableCheckpointError('Checkpoint identity/hash mismatch')
    if json.loads(state_bytes).get('schema_version') != 2:
        raise DurableCheckpointError('Unexpected state schema')
    return root, state_path, state_bytes


def _persist_snapshot(root, state_path, state_bytes, commit_message):
    state_digest = hashlib.sha256(state_bytes).hexdigest()
    for args in [('diff', '--name-only', '-z'), ('diff', '--cached', '--name-only', '-z')]:
        paths = _git(root, *args).stdout.decode().split('\0')
        if any(path and path != STATE_NAME for path in paths):
            raise DurableCheckpointError('Refusing unrelated worktree or staged changes')

    # Reject unpublished code commits too, not just currently staged paths.
    _git(root, 'fetch', 'origin', 'main')
    if _git(root, 'rev-list', '--merges', 'FETCH_HEAD..HEAD').stdout.strip():
        raise DurableCheckpointError('Refusing unpublished merge commits')
    unpublished = _git(root, 'log', '-m', '--format=', '--name-only', 'FETCH_HEAD..HEAD').stdout.decode().splitlines()
    if any(path and path != STATE_NAME for path in unpublished):
        raise DurableCheckpointError('Refusing unrelated unpublished commits')

    _git(root, 'config', 'user.name', 'github-actions[bot]')
    _git(root, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    _git(root, 'add', '--', STATE_NAME)
    if _git(root, 'diff', '--cached', '--quiet', check=False).returncode:
        _git(root, 'commit', '-m', commit_message)

    for _ in range(3):
        _git(root, 'fetch', 'origin', 'main')
        remote_commit = _git(root, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        common = _git(root, 'merge-base', 'HEAD', remote_commit).stdout.decode().strip()
        changed = _git(root, 'diff', '--name-only', common, remote_commit, '--', STATE_NAME).stdout
        if changed:
            # Even a clean, nonconflicting state merge could invalidate our
            # in-memory snapshot. Never rebase over another writer's state.
            raise DurableCheckpointError('Remote state changed; no Telegram attempt authorized')
        rebase = _git(root, 'rebase', remote_commit, check=False)
        if rebase.returncode:
            _git(root, 'rebase', '--abort', check=False)
            raise DurableCheckpointError('Remote state conflict; no Telegram attempt authorized')
        if hashlib.sha256(state_path.read_bytes()).hexdigest() != state_digest:
            raise DurableCheckpointError('State changed during rebase; no send authorized')
        push = _git(root, 'push', 'origin', 'HEAD:refs/heads/main', check=False)
        fetch = _git(root, 'fetch', 'origin', 'main', check=False)
        if fetch.returncode:
            raise DurableCheckpointError('Remote checkpoint readback unavailable; no send authorized')
        remote_state = _git(root, 'show', 'FETCH_HEAD:' + STATE_NAME, check=False)
        if remote_state.returncode == 0 and hashlib.sha256(remote_state.stdout).hexdigest() == state_digest:
            commit = _git(root, 'rev-parse', 'FETCH_HEAD').stdout.decode().strip()
            if len(commit) == 40 and all(c in '0123456789abcdef' for c in commit):
                return commit
        if push.returncode == 0:
            # A later writer changed the state before readback. Do not overwrite it.
            raise DurableCheckpointError('Published intent is no longer current; no send authorized')
        # A rejected/uncertain push is only retried after reading the remote. A
        # same-state conflict will fail the next rebase instead of overwriting it.
    raise DurableCheckpointError('Remote checkpoint was not verified; no send authorized')


def persist_intent(state_path, marker_path, item_key, message_digest, destination_digest,
                   *, expected_remote=EXPECTED_ORIGIN):
    """Verify this exact held intent on remote main before authorizing a send.

    expected_remote is explicit only so offline tests can use file remotes;
    production callers use the fixed GitHub origin default.
    """
    root, state_path, state_bytes = _validate_checkpoint(state_path, marker_path, expected_remote)
    state = json.loads(state_bytes)
    intent = (state.get('pending_items') or {}).get(item_key) or {}
    if (intent.get('status') != 'delivery_unknown' or intent.get('stage') != 'send_prepared'
            or (intent.get('item') or {}).get('key') != item_key
            or intent.get('message_sha256') != message_digest
            or intent.get('destination_sha256') != destination_digest):
        raise DurableCheckpointError('Exact prepared delivery intent is missing')
    return _persist_snapshot(root, state_path, state_bytes, 'Checkpoint Tesla delivery intent [skip ci]')


def persist_final_state(state_path, marker_path, *, expected_remote=EXPECTED_ORIGIN):
    """Persist outcomes with the same destination, scope and conflict safeguards."""
    root, state_path, state_bytes = _validate_checkpoint(state_path, marker_path, expected_remote)
    return _persist_snapshot(root, state_path, state_bytes, 'Update semiconductor consumption monitor state [skip ci]')


if __name__ == '__main__':
    commit = persist_final_state(STATE_NAME, 'out/semiconductor_consumption_checkpoint.json')
    print('tesla_state_persisted_commit=' + commit)
