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


class Layouts(unittest.TestCase):
    RECT = [0, 0, 1000, 600]

    def test_focus_gives_the_first_window_the_left_sixty_percent(self):
        # Arrange
        rows = [{}] * 4
        # Act
        rects = cs.layout_rects(rows, 'focus', rect=self.RECT)
        # Assert
        self.assertEqual(rects[0], [4, 4, 596, 596])
        self.assertTrue(all(r[0] >= 600 for r in rects[1:]))  # the rest share the right strip
        self.assertEqual([r[1] for r in rects[1:]], [4, 204, 404])  # stacked, one above the other

    def test_focus_with_one_window_is_just_the_grid(self):
        self.assertEqual(cs.layout_rects([{}], 'focus', rect=self.RECT), [[4, 4, 996, 596]])

    def test_columns_puts_every_window_side_by_side(self):
        rects = cs.layout_rects([{}] * 3, 'columns', rect=[0, 0, 900, 600])
        self.assertEqual([r[0] for r in rects], [4, 304, 604])
        self.assertTrue(all(r[3] == 596 for r in rects))


class Displays(unittest.TestCase):
    SCREENS = [{'name': 'Studio', 'rect': [0, 30, 2560, 1380]},
               {'name': 'LG HDR 4K', 'rect': [2560, 110, 5120, 1550]}]

    def test_screen_rect_flips_appkit_coordinates(self):
        # Arrange: a display to the right whose bottom sits 110 px below the main one's
        sc = {'name': 'LG', 'x': 2560, 'y': -110, 'w': 2560, 'h': 1440}
        # Act
        rect = cs.screen_rect(sc, 1440)['rect']
        # Assert
        self.assertEqual(rect, [2560, 110, 5120, 1550])

    def test_display_rect_by_main_number_or_name(self):
        self.assertEqual(cs.display_rect('main', self.SCREENS), [0, 30, 2560, 1380])
        self.assertEqual(cs.display_rect('2', self.SCREENS), [2560, 110, 5120, 1550])
        self.assertEqual(cs.display_rect('lg', self.SCREENS), [2560, 110, 5120, 1550])
        self.assertIsNone(cs.display_rect('3', self.SCREENS))
        self.assertIsNone(cs.display_rect('dell', self.SCREENS))
