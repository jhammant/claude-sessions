import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skill/scripts'
sys.path.insert(0, str(SCRIPTS))
from codex_transcripts import read_rollout, export_rollout, sync_all
from codex_feed import run_hook

loader = importlib.machinery.SourceFileLoader('session_manager', str(SCRIPTS / 'codex-sessions'))
spec = importlib.util.spec_from_loader(loader.name, loader)
manager = importlib.util.module_from_spec(spec)
loader.exec_module(manager)

SID = '11111111-1111-4111-8111-111111111111'


def record(kind, payload):
    return {'type': kind, 'timestamp': '2026-09-07T12:00:00Z', 'payload': payload}


def fixture(source='cli'):
    return [record('session_meta', {'id': SID, 'cwd': '/work/api', 'source': source}),
            record('event_msg', {'type': 'task_started', 'turn_id': 'turn-1'}),
            record('response_item', {'type': 'message', 'role': 'developer', 'content': 'not human'}),
            record('response_item', {'type': 'message', 'role': 'user', 'content': '<environment_context>ignored</environment_context>'}),
            record('response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Explain backpressure ' * 25}]}),
            record('event_msg', {'type': 'item_completed', 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': 'Explain backpressure ' * 25}]}}),
            record('response_item', {'type': 'reasoning', 'summary': 'PRIVATE REASONING'}),
            record('response_item', {'type': 'function_call_output', 'output': 'PRIVATE OUTPUT'}),
            record('response_item', {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Bound queues to control memory.'}]}),
            record('compacted', {'replacement_history': [{'role': 'user', 'content': 'replayed'}]})]


def write_fixture(path, rows):
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n{unfinished')


class TranscriptTests(unittest.TestCase):
    def test_prose_normalization_dedup_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'rollout.jsonl'
            write_fixture(file, fixture())
            session = read_rollout(file)
            self.assertEqual([r['type'] for r in session['records']], ['user', 'assistant'])
            self.assertEqual(session['records'][0]['sessionId'], 'codex-' + SID)
            self.assertNotIn('PRIVATE', json.dumps(session))

    def test_repeated_human_text_in_later_turn_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'r.jsonl'
            rows = fixture() + [record('event_msg', {'type': 'task_started', 'turn_id': 'turn-2'}), fixture()[4], fixture()[5]]
            write_fixture(file, rows)
            self.assertEqual(len(read_rollout(file)['records']), 3)

    def test_event_only_fallback_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'r.jsonl'
            write_fixture(file, [fixture()[0], None, [], record('event_msg', {'type': 'user_message', 'message': 'event fallback'})])
            self.assertEqual(read_rollout(file)['records'][0]['message']['content'], 'event fallback')

    def test_subagents_and_exec_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'r.jsonl'
            for source in ('exec', {'subagent': {'parent_thread_id': SID}}):
                write_fixture(file, fixture(source))
                self.assertIsNone(read_rollout(file))

    def test_inherited_parent_metadata_cannot_relabel_subagent_as_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'r.jsonl'
            rows = fixture({'subagent': {'thread_spawn': {'parent_thread_id': SID}}})
            rows.append(fixture()[0])
            write_fixture(file, rows)
            self.assertIsNone(read_rollout(file))

    def test_redaction_and_private_idempotent_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'r.jsonl'
            rows = fixture()
            rows.append(record('response_item', {'type': 'message', 'role': 'user', 'content': 'api_key=supersecretvalue'}))
            write_fixture(file, rows)
            session = export_rollout(file, root / 'feed')
            output = Path(session['file'])
            before = output.stat().st_mtime_ns
            export_rollout(file, root / 'feed')
            self.assertEqual(output.stat().st_mtime_ns, before)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('supersecretvalue', output.read_text())

    def test_hook_replay_and_resume_queue_only_delta(self):
        learning = Path(os.environ.get('TEST_CL_SCRIPT', '/nonexistent'))
        if not learning.exists():
            self.skipTest('TEST_CL_SCRIPT must point to cl.py for integration test')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CL_HOME': tmp}):
            root = Path(tmp)
            file = root / 'r.jsonl'
            rows = fixture()
            write_fixture(file, rows)
            data = {'session_id': SID, 'transcript_path': str(file)}
            self.assertTrue(run_hook(data, learning, root / 'feed'))
            self.assertFalse(run_hook(data, learning, root / 'feed'))
            rows += [record('event_msg', {'type': 'task_started', 'turn_id': 'turn-2'}),
                     record('response_item', {'type': 'message', 'role': 'user', 'content': 'Now explain flow control. ' * 25})]
            write_fixture(file, rows)
            self.assertTrue(run_hook(data, learning, root / 'feed'))
            queued = (root / 'queue.txt').read_text().splitlines()
            self.assertEqual(len(queued), 2)
            second = Path(queued[1].split('\t')[1]).read_text()
            self.assertNotIn('backpressure', second)
            digest = subprocess.check_output([sys.executable, str(learning), 'digest-transcript', queued[1].split('\t')[1]], text=True)
            self.assertIn('flow control', digest)

    def test_off_mode_never_queues(self):
        learning = Path(os.environ.get('TEST_CL_SCRIPT', '/nonexistent'))
        if not learning.exists():
            self.skipTest('TEST_CL_SCRIPT not set')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CL_HOME': tmp}):
            root = Path(tmp)
            (root / 'settings.json').write_text('{"mode":"off"}')
            file = root / 'r.jsonl'
            write_fixture(file, fixture())
            self.assertFalse(run_hook({'transcript_path': str(file)}, learning, root / 'feed'))
            self.assertFalse((root / 'queue.txt').exists())

    def test_extractor_applies_evidence_once_without_model_calls(self):
        learning = Path(os.environ.get('TEST_CL_SCRIPT', '/nonexistent'))
        if not learning.exists():
            self.skipTest('TEST_CL_SCRIPT not set')
        extractor = Path(__file__).with_name('fixture_extractor.py')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'CL_HOME': tmp, 'CL_EXTRACT_CMD': f'{sys.executable} {extractor}'
        }):
            root = Path(tmp)
            file = root / 'r.jsonl'
            write_fixture(file, fixture())
            self.assertTrue(run_hook({'transcript_path': str(file)}, learning, root / 'feed'))
            for _ in range(2):
                subprocess.run(['bash', str(learning.with_name('extract-events.sh'))],
                               check=True, capture_output=True, timeout=20)
            events = (root / 'events.jsonl').read_text().splitlines()
            self.assertEqual(len(events), 1)
            self.assertTrue(json.loads(events[0])['session'].startswith('codex-'))
            self.assertEqual(json.loads((root / 'concepts.json').read_text())['backpressure']['evidence'], 1)


class SessionsTests(unittest.TestCase):
    def test_resume_argv_quoting_and_no_permission_bypass(self):
        import shlex
        command = manager.build_cmd({'cwd': '/work/project $(touch bad)', 'session': 'codex-' + SID})
        self.assertEqual(shlex.split(command), ['cd', '/work/project $(touch bad)', '&&', 'codex', 'resume', SID])
        with self.assertRaises(ValueError):
            manager.build_cmd({'cwd': '/work', 'session': '; touch bad'})

    def test_process_filter(self):
        self.assertTrue(manager.is_codex_cli('/usr/bin/codex resume ' + SID))
        self.assertTrue(manager.is_codex_cli('codex -p work'))
        for command in ('claude', 'codex exec task', 'codex app-server', 'codex mcp-server', 'node service.js'):
            self.assertFalse(manager.is_codex_cli(command))

    def test_unknown_window_not_guessed(self):
        with patch.object(manager, 'sh', return_value=(0, '', '')):
            result = manager.match_sessions([{'pid': 123, 'resumed': None, 'cwd': '/work'}])
            self.assertIsNone(result[0]['session'])

    def test_library_has_app_threads_and_resume_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'r.jsonl'
            write_fixture(file, fixture('vscode'))
            export_rollout(file, root / 'feed')
            with patch.object(manager, 'PROJ', str(root / 'feed/projects')):
                rows = manager.scan_library(deep=True)
            self.assertEqual(len(rows), 1)
            self.assertIn('backpressure', rows[0]['first'])
            self.assertIn(SID, manager.build_cmd(rows[0]))


if __name__ == '__main__':
    unittest.main()
