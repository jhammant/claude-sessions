"""Local, version-tolerant Codex rollouts -> redacted compatibility transcripts.

Never reads reasoning, tool output, attachments or compaction replacement history.
The on-disk rollout schema is not a stable Codex API; keep fixtures for new versions.
"""
import collections
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def codex_root():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))


def feed_root():
    return Path(os.environ.get('CODEX_TRANSCRIPT_FEED', str(codex_root() / 'transcript-feed')))


SYNTHETIC = ('<environment_context>', '<system-reminder>', '<turn_aborted>',
             '<user_instructions>', '# AGENTS.md instructions', '<permissions',
             '<skills_instructions>', 'Base directory for this skill:',
             '<subagent_notification>', '<task_notification>')


def redact(text):
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'\b(?:sk-(?:ant-)?[\w-]{16,}|gh[pousr]_[\w]{20,}|AKIA[A-Z0-9]{16})\b',
                  '[REDACTED TOKEN]', text)
    text = re.sub(r'(?i)(\b(?:authorization\s*:\s*bearer|api[_-]?key|secret[_-]?key|password|access[_-]?token)\s*[=:]?\s*)["\']?[^\s"\'`,;]+',
                  r'\1[REDACTED]', text)
    return text


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(b['text'] for b in content if isinstance(b, dict)
                     and b.get('type') in ('text', 'input_text', 'output_text')
                     and isinstance(b.get('text'), str))


def read_rollout(path):
    meta = {}
    primary, fallback = [], []
    turn = ''
    with Path(path).open(encoding='utf-8', errors='replace') as source:
        for number, line in enumerate(source):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get('payload'), dict):
                continue
            payload = obj['payload']
            kind = obj.get('type')
            stamp = obj.get('timestamp', '')
            if kind == 'session_meta':
                # Fork/subagent rollouts can embed the parent's metadata later.
                # The first header identifies this physical thread.
                if not meta:
                    meta = payload
                continue
            if kind == 'turn_context' or (kind == 'event_msg' and payload.get('type') == 'task_started'):
                turn = payload.get('turn_id', turn)
            role, text, identity = None, '', ''
            target = primary
            if kind == 'response_item' and payload.get('type') == 'message':
                if payload.get('channel') == 'analysis':
                    continue
                role = payload.get('role')
                text = text_content(payload.get('content'))
                identity = payload.get('id')
            elif kind == 'event_msg':
                target = fallback
                if payload.get('type') == 'item_completed':
                    item = payload.get('item') or {}
                    if not isinstance(item, dict):
                        continue
                    role = {'UserMessage': 'user', 'AgentMessage': 'assistant'}.get(item.get('type'))
                    text = text_content(item.get('content'))
                    identity = item.get('id')
                elif payload.get('type') in ('user_message', 'agent_message'):
                    role = 'user' if payload['type'] == 'user_message' else 'assistant'
                    text = payload.get('message', '')
            if role not in ('user', 'assistant') or not isinstance(text, str):
                continue
            text = text.strip()
            if not text or (role == 'user' and text.startswith(SYNTHETIC)):
                continue
            target.append({'role': role, 'text': text, 'turn': turn,
                           'timestamp': stamp, 'order': number, 'id': identity})
    sid = meta.get('id') or meta.get('session_id')
    try:
        sid = str(uuid.UUID(sid))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Rollout has no valid session UUID')
    source = meta.get('source')
    interactive = source in ('cli', 'vscode', 'app', 'desktop') if isinstance(source, str) else False
    # Explicitly exclude subagents and headless jobs from human learning and windows.
    if meta.get('parent_thread_id') or not interactive:
        return None
    cwd = meta.get('cwd')
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError('Rollout has no absolute working directory')
    counts = collections.Counter((m['turn'], m['role'], m['text']) for m in primary)
    kept = list(primary)
    fallback_seen = set()
    for m in fallback:
        key = (m['turn'], m['role'], m['text'])
        if counts[key]:
            counts[key] -= 1
        elif (key, m['id']) not in fallback_seen:
            kept.append(m)
        fallback_seen.add((key, m['id']))
    kept.sort(key=lambda m: m['order'])
    records = []
    seen_ids = set()
    for m in kept:
        if m['id'] and m['id'] in seen_ids:
            continue
        if m['id']:
            seen_ids.add(m['id'])
        records.append({'type': m['role'], 'source_provider': 'codex',
                        'entrypoint': 'cli' if source == 'cli' else 'app',
                        'sessionId': 'codex-' + sid, 'cwd': cwd,
                        'gitBranch': meta.get('git', {}).get('branch', '') if isinstance(meta.get('git'), dict) else '',
                        'timestamp': m['timestamp'],
                        'uuid': m['id'] or f'codex-{sid}-{m["order"]}',
                        'message': {'role': m['role'], 'content': redact(m['text'])}})
    return {'id': sid, 'cwd': cwd, 'source': source,
            'started': meta.get('timestamp', ''), 'records': records}


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def feed_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def export_rollout(path, root=None):
    root = Path(root) if root is not None else feed_root()
    session = read_rollout(path)
    if not session or not session['records']:
        return None
    project = re.sub(r'[/.\x00-\x1f]', '-', session['cwd'])
    target = root / 'projects' / project / ('codex-' + session['id'] + '.jsonl')
    content = ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n'
                      for r in session['records'])
    if not target.exists() or target.read_text() != content:
        atomic_write(target, content)
    session['file'] = str(target)
    return session


def sync_all(root=None, sessions_root=None):
    root = Path(root) if root is not None else feed_root()
    sessions_root = Path(sessions_root) if sessions_root is not None else codex_root() / 'sessions'
    stats = {'exported': 0, 'skipped': 0, 'errors': 0}
    with feed_lock(root):
        for file in sessions_root.rglob('*.jsonl'):
            try:
                result = export_rollout(file, root)
                stats['exported' if result else 'skipped'] += 1
            except (OSError, ValueError, TypeError):
                stats['errors'] += 1
    return stats
