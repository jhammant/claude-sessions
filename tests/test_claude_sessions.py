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


def area(r):
    return (r[2] - r[0]) * (r[3] - r[1])


def covers(rects, rect, gap=4):
    """The tiles, grown back by the gap, fill rect exactly: no holes, no overlaps."""
    grown = [[r[0] - gap, r[1] - gap, r[2] + gap, r[3] + gap] for r in rects]
    if sum(area(g) for g in grown) != area(rect):
        return False
    for i, a in enumerate(grown):
        for b in grown[i + 1:]:
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                return False
    return all(rect[0] <= g[0] and g[2] <= rect[2] and rect[1] <= g[1] and g[3] <= rect[3] for g in grown)


class Tiling(unittest.TestCase):
    SCREEN = [0, 25, 2560, 1415]

    def test_grid_leaves_no_empty_cells(self):
        for n, cols in ((5, 3), (7, 3), (3, 2), (17, 5)):
            rects = cs.layout_rects([{}] * n, 'grid', cols=cols, rect=self.SCREEN)
            self.assertEqual(len(rects), n)
            self.assertTrue(covers(rects, self.SCREEN), (n, cols))

    def test_smart_gives_the_window_used_last_the_most_room_and_fills_the_screen(self):
        rects = cs.layout_rects([{}] * 4, 'smart', rect=self.SCREEN)
        areas = [area(r) for r in rects]
        self.assertEqual(areas, sorted(areas, reverse=True))
        self.assertGreater(areas[0], 1.5 * areas[-1])
        self.assertEqual(rects[0][:2], [4, 29])  # top left
        self.assertTrue(covers(rects, self.SCREEN))

    def test_smart_evens_out_when_there_is_no_room(self):
        small = [0, 0, 1440, 900]
        rects = cs.layout_rects([{}] * 9, 'smart', rect=small)
        self.assertTrue(covers(rects, small))
        self.assertTrue(all(r[2] - r[0] >= 400 and r[3] - r[1] >= 200 for r in rects))
        self.assertEqual(len({area(r) for r in rects}), 1)  # a plain grid: 9 equal tiles

    def test_each_window_stays_on_its_screen(self):
        screens = [{'name': 'Studio', 'rect': [0, 30, 2560, 1380]},
                   {'name': 'LG', 'rect': [2560, 110, 5120, 1550]}]
        wins = [{'id': 'a', 'bounds': [3000, 200, 3800, 900]}, {'id': 'b', 'bounds': [100, 100, 900, 700]},
                {'id': 'c', 'bounds': [5300, 300, 5900, 800]}, {'id': 'd'}]
        groups = cs.split_by_display(wins, screens)
        self.assertEqual([[w['id'] for w in g] for _, g in groups], [['b', 'd'], ['a', 'c']])
        self.assertEqual(cs.split_by_display(wins, []), [(None, wins)])

    def test_smart_arrange_puts_the_frontmost_window_first(self):
        wins = [{'tty': '/dev/ttys001', 'cwd': '/w/old'}, {'tty': '/dev/ttys002', 'cwd': '/w/front'}]
        geo = {'/dev/ttys001': {'term': 'iTerm2', 'bounds': [0, 0, 800, 600], 'z': 3},
               '/dev/ttys002': {'term': 'iTerm2', 'bounds': [800, 0, 1600, 600], 'z': 0}}
        args = type('A', (), {'mode': 'smart', 'busy_first': False, 'cols': None, 'rect': None,
                              'display': None, 'dry_run': True})()
        with patch.object(cs, 'match_sessions', return_value=wins), patch.object(cs, 'discover', return_value=[]), \
                patch.object(cs, 'geometry', return_value=geo), patch.object(cs, 'terminal_app', return_value='iTerm2'), \
                patch.object(cs, 'displays', return_value=[{'name': 'Studio', 'rect': [0, 25, 2560, 1415]}]), \
                patch('builtins.print') as out:
            cs.arrange_once(args)
        lines = [c.args[0] for c in out.call_args_list if c.args and '->' in str(c.args[0])]
        self.assertIn('/w/front', lines[0])
        first, second = [eval(line.split('->')[1]) for line in lines]
        self.assertGreater(area(first), area(second))


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
