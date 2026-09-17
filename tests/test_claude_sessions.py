import importlib.machinery
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/scripts/claude-sessions'
loader = importlib.machinery.SourceFileLoader('claude_sessions', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
cs = importlib.util.module_from_spec(spec)
loader.exec_module(cs)

SID = '11111111-1111-4111-8111-111111111111'


class LaunchFlagsTest(unittest.TestCase):
    def test_keeps_skip_permissions(self):
        self.assertEqual(cs.launch_flags('claude --dangerously-skip-permissions --resume ' + SID),
                         ['--dangerously-skip-permissions'])

    def test_keeps_permission_mode_in_both_spellings(self):
        self.assertEqual(cs.launch_flags('claude --permission-mode plan'), ['--permission-mode', 'plan'])
        self.assertEqual(cs.launch_flags('claude --permission-mode=acceptEdits'), ['--permission-mode=acceptEdits'])

    def test_ignores_lookalikes_and_other_args(self):
        self.assertEqual(cs.launch_flags('claude --allow-dangerously-skip-permissions --model opus'), [])
        self.assertEqual(cs.launch_flags('claude --resume ' + SID), [])


class BuildCmdTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('CLAUDE_SESSIONS_CLAUDE_ARGS', None)

    def test_replays_recorded_flags(self):
        row = {'cwd': '/w/api', 'session': SID, 'flags': ['--dangerously-skip-permissions']}
        self.assertEqual(cs.build_cmd(row),
                         'cd /w/api && claude --dangerously-skip-permissions --resume ' + SID)

    def test_default_args_fill_in_when_nothing_recorded(self):
        os.environ['CLAUDE_SESSIONS_CLAUDE_ARGS'] = '--dangerously-skip-permissions'
        self.assertEqual(cs.build_cmd({'cwd': '/w/api', 'session': SID, 'flags': []}),
                         'cd /w/api && claude --dangerously-skip-permissions --resume ' + SID)
        self.assertEqual(cs.build_cmd({'cwd': '/w/api', 'session': None}),
                         'cd /w/api && claude --dangerously-skip-permissions')

    def test_recorded_flags_beat_the_default(self):
        os.environ['CLAUDE_SESSIONS_CLAUDE_ARGS'] = '--dangerously-skip-permissions'
        row = {'cwd': '/w/api', 'session': SID, 'flags': ['--permission-mode', 'plan']}
        self.assertEqual(cs.build_cmd(row), 'cd /w/api && claude --permission-mode plan --resume ' + SID)

    def test_no_flags_and_no_default_is_unchanged(self):
        self.assertEqual(cs.build_cmd({'cwd': '/w/my api', 'session': SID}),
                         "cd '/w/my api' && claude --resume " + SID)


class SaveTest(unittest.TestCase):
    def test_save_records_each_windows_flags(self):
        window = {'pid': 1, 'ppid': 0, 'tty': '/dev/ttys001', 'start': 0, 'cwd': '/w/api',
                  'cmd': 'claude --dangerously-skip-permissions --resume ' + SID,
                  'resumed': SID, 'flags': ['--dangerously-skip-permissions'],
                  'session': SID, 'match': 'resume'}
        args = type('A', (), {'replace': True, 'keep_hours': 72})()
        with patch.object(cs, 'match_sessions', return_value=[window]), \
                patch.object(cs, 'discover', return_value=[]), \
                patch.object(cs, 'geometry', return_value={}), \
                patch.object(cs, 'write_snapshot') as write, \
                patch('builtins.print'):
            cs.cmd_save(args)
        saved = write.call_args[0][0]
        self.assertEqual(saved[0]['flags'], ['--dangerously-skip-permissions'])


if __name__ == '__main__':
    unittest.main()
