#!/usr/bin/env python3
"""Synchronize Codex history and queue immutable, non-overlapping learning batches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from codex_transcripts import (atomic_write, codex_root, export_rollout, feed_lock,
                               feed_root, sync_all)


def fingerprint(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def queue_learning(session, script, root):
    """Caller holds feed lock. Queue only unseen text, never whole-session replay."""
    env = dict(os.environ)
    mode = subprocess.run([sys.executable, str(script), 'get-mode'], env=env,
                          capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    state_file = root / 'learning-cursors' / (session['id'] + '.json')
    records = session['records']
    state = json.loads(state_file.read_text()) if state_file.exists() else {'count': 0}
    count = state['count']
    if count > len(records) or (count and state.get('prefix') != fingerprint(records[:count])):
        raise ValueError('Transcript prefix changed; refusing to replay learning evidence')
    if mode == 'off':
        # Disabled means do not learn later from turns observed while disabled.
        atomic_write(state_file, json.dumps({'count': len(records), 'prefix': fingerprint(records)}))
        return False
    delta = records[count:]
    if sum(len(r['message']['content']) for r in delta) < 400:
        return False
    key = 'codex-' + session['id'] + '-' + fingerprint(delta)[:16]
    batch = root / 'learning-batches' / (key + '.jsonl')
    content = ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in delta)
    if not batch.exists():
        atomic_write(batch, content)
    elif batch.read_text() != content:
        raise ValueError('Learning batch collision')
    result = subprocess.run([sys.executable, str(script), 'queue-add', key, str(batch)],
                            env=env, capture_output=True, text=True, timeout=10)
    if result.returncode and 'already queued or done' not in result.stdout:
        raise RuntimeError('Learning queue failed: ' + result.stderr[:200])
    atomic_write(state_file, json.dumps({'count': len(records), 'prefix': fingerprint(records)}))
    return result.returncode == 0


def run_hook(data, learning_script=None, root=None):
    if not isinstance(data, dict):
        raise ValueError('Hook input must be a JSON object')
    if os.environ.get('CL_EXTRACT'):
        return False
    path = data.get('transcript_path')
    if not path:
        return False
    root = Path(root) if root is not None else feed_root()
    with feed_lock(root):
        session = export_rollout(path, root)
        if not session:
            return False
        if data.get('session_id') and data['session_id'] != session['id']:
            raise ValueError('Hook session ID does not match transcript')
        return queue_learning(session, learning_script, root) if learning_script else False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sync', 'hook'])
    parser.add_argument('--learning-script', type=Path)
    parser.add_argument('--extract-script', type=Path)
    parser.add_argument('--summary-script', type=Path)
    args = parser.parse_args()
    if args.command == 'sync':
        print(json.dumps(sync_all()))
        return 0
    try:
        queued = run_hook(json.load(sys.stdin), args.learning_script)
        if args.summary_script and not os.environ.get('CL_EXTRACT'):
            subprocess.run(['node', str(args.summary_script)], check=True,
                           capture_output=True, timeout=15)
        if queued and args.extract_script:
            logs = feed_root() / 'logs'
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(logs / 'learning.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as log:
                subprocess.Popen(['bash', str(args.extract_script)], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # A learning outage must not block the user's turn; leave a diagnostic.
        print('Codex feed: ' + str(exc), file=sys.stderr)
    print('{}')  # Stop hooks require valid JSON, including on no-op/error paths.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
