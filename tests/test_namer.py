import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/scripts/namer.py'
spec = importlib.util.spec_from_file_location('namer', SCRIPT)
nm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nm)

RULE = '─' * 60


def screen(input_line, extra_above=()):
    return '\n'.join(list(extra_above) + ['✻ Baked for 4s', '', RULE, input_line, RULE,
                                          '    ghost    …/proj   Opus', '  ⏵⏵ bypass permissions on']) + '\n'


class CleanTest(unittest.TestCase):
    def test_strips_quotes_label_and_full_stop(self):
        self.assertEqual(nm.clean('Name: "Shop · App Store go-live."'), 'Shop · App Store go-live')

    def test_normalises_other_separators(self):
        self.assertEqual(nm.clean('Ledger - nightly backup fix'), 'Ledger · nightly backup fix')

    def test_cuts_long_names_at_a_word(self):
        name = nm.clean('Acme · conference print order and booklet artwork for Wednesday')
        self.assertLessEqual(len(name), nm.MAX_LEN)
        self.assertFalse(name.endswith(' '))
        self.assertTrue(name.startswith('Acme · conference print'))

    def test_drops_thinking_and_empty_output(self):
        self.assertEqual(nm.clean('<think>hmm</think>\nshop · passkeys'), 'shop · passkeys')
        self.assertIsNone(nm.clean('   \n'))
        self.assertIsNone(nm.clean(None))


class DecideTest(unittest.TestCase):
    def test_first_prompt_asks_for_a_name(self):
        emit, st, due = nm.decide({}, {'session_title': None}, None, now=1000)
        self.assertIsNone(emit)
        self.assertTrue(due)
        self.assertEqual(st['prompts'], 1)

    def test_pending_name_is_emitted_once(self):
        st = {'prompts': 3, 'pending': 'shop · passkeys', 'named_prompt': 3, 'named_at': 1000}
        emit, st, due = nm.decide(st, {'session_title': 'dev-4c'}, None, now=1010)
        self.assertEqual(emit, 'shop · passkeys')
        self.assertEqual(st['applied'], 'shop · passkeys')
        self.assertNotIn('pending', st)
        self.assertFalse(due)

    def test_hand_rename_locks_the_session(self):
        st = {'prompts': 4, 'applied': 'Ledger · backups', 'pending': 'Ledger · restores',
              'named_prompt': 4, 'named_at': 0}
        emit, st, due = nm.decide(st, {'session_title': 'my name'}, 'my name', now=99999)
        self.assertIsNone(emit)
        self.assertTrue(st['locked'])
        self.assertFalse(due)

    def test_launch_name_counts_as_by_hand(self):
        emit, st, _ = nm.decide({}, {'session_title': 'worker 77'}, 'worker 77', now=5)
        self.assertTrue(st['locked'])
        self.assertIsNone(emit)

    def test_ai_title_is_not_a_hand_rename(self):
        # Claude Code's own first-prompt title is not a custom title, so it may be replaced.
        _, st, due = nm.decide({}, {'session_title': 'Reply with ok'}, None, now=5)
        self.assertFalse(st.get('locked'))
        self.assertTrue(due)

    def test_refresh_waits_for_prompts_and_time(self):
        st = {'prompts': 2, 'applied': 'x · y', 'named_prompt': 1, 'named_at': 1000}
        self.assertFalse(nm.decide(st, {'session_title': 'x · y'}, 'x · y', now=1000 + 99999)[2])
        st['prompts'] = 9
        self.assertFalse(nm.decide(st, {'session_title': 'x · y'}, 'x · y', now=1060)[2])
        self.assertTrue(nm.decide(st, {'session_title': 'x · y'}, 'x · y', now=1000 + nm.REFRESH_SECS)[2])

    def test_stuck_suggester_is_forgotten(self):
        st = {'prompts': 1, 'suggesting_since': 0}
        _, st, due = nm.decide(st, {}, None, now=10 ** 6)
        self.assertNotIn('suggesting_since', st)


class InputIsEmptyTest(unittest.TestCase):
    def test_empty_box(self):
        self.assertTrue(nm.input_is_empty(screen('❯\xa0 ')))

    def test_placeholder_counts_as_empty(self):
        self.assertTrue(nm.input_is_empty(screen('❯ Try "fix lint errors"')))

    def test_half_typed_prompt(self):
        self.assertFalse(nm.input_is_empty(screen('❯ can you also check the')))

    def test_permission_dialog(self):
        dialog = '\n'.join(['Do you want to proceed?', '❯ 1. Yes', '  2. No', '', 'Esc to cancel'])
        self.assertFalse(nm.input_is_empty(dialog))

    def test_question_menu_without_rules(self):
        self.assertFalse(nm.input_is_empty('Pick one\n❯ 1. Option A\n  2. Option B\nEnter to select\n'))

    def test_answered_menu_in_scrollback_is_history(self):
        history = ['  6. Chat about this', '', 'Enter to select · Tab/Arrow keys to navigate · Esc to cancel',
                   '❯ or something like that', '     (Recommended)', 'more output']
        self.assertTrue(nm.input_is_empty(screen('❯ ', extra_above=history)))

    def test_box_title_rule_still_counts(self):
        s = '\n'.join(['', '──── namer-test · title works ─', '❯ ', RULE, 'status']) + '\n'
        self.assertTrue(nm.input_is_empty(s))


class TranscriptTest(unittest.TestCase):
    def write(self, records):
        fd, path = tempfile.mkstemp(suffix='.jsonl')
        with os.fdopen(fd, 'w') as fh:
            for r in records:
                fh.write(json.dumps(r) + '\n')
        self.addCleanup(os.remove, path)
        return path

    def test_recent_keeps_real_prompts_only(self):
        path = self.write([
            {'type': 'user', 'cwd': '/w/a', 'message': {'content': 'first job'}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'x'}]}},
            {'type': 'user', 'message': {'content': '<command-name>/clear</command-name>'}},
            {'type': 'user', 'message': {'content': 'This session is being continued from a previous...'}},
            {'type': 'user', 'isSidechain': True, 'message': {'content': 'subagent prompt'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'done it'}]}},
            {'type': 'user', 'cwd': '/w/b', 'gitBranch': 'feat/x', 'message': {'content': 'now the second'}},
        ])
        info = nm.recent(path)
        self.assertEqual(info['prompts'], ['first job', 'now the second'])
        self.assertEqual(info['reply'], 'done it')
        self.assertEqual((info['cwd'], info['branch']), ('/w/b', 'feat/x'))

    def test_last_custom_title_wins(self):
        path = self.write([
            {'type': 'custom-title', 'customTitle': 'old'},
            {'type': 'user', 'message': {'content': 'hi'}},
            {'type': 'custom-title', 'customTitle': 'new · name'},
            {'type': 'ai-title', 'aiTitle': 'not this'},
        ])
        self.assertEqual(nm.last_custom_title(path), 'new · name')

    def test_pasted_text_is_collapsed(self):
        t = nm.clip_prompt('fix this <pasted_content id="a1">secret stuff</pasted_content id="a1"> please')
        self.assertNotIn('secret', t)
        self.assertIn('[pasted text]', t)


class ProjectOfTest(unittest.TestCase):
    def test_worktree_suffix_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, 'shop--passkeys')
            os.makedirs(os.path.join(repo, 'packages', 'server'))
            subprocess.run(['git', 'init', '-q', repo], check=True)
            self.assertEqual(nm.project_of(os.path.join(repo, 'packages', 'server')), 'shop')

    def test_home_is_not_a_project(self):
        self.assertIsNone(nm.project_of(os.path.expanduser('~')))


if __name__ == '__main__':
    unittest.main()


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.saved = nm.ask_lmstudio, nm.ask_claude
        self.addCleanup(lambda: (setattr(nm, 'ask_lmstudio', self.saved[0]), setattr(nm, 'ask_claude', self.saved[1])))
        self.seen = []
        nm.ask_lmstudio = lambda prompt: None
        nm.ask_claude = lambda prompt: (self.seen.append(prompt), 'Recipe · starter names')[1]

    def test_first_prompt_comes_from_the_hook_not_the_transcript(self):
        # UserPromptSubmit runs before the prompt reaches the transcript.
        name, source = nm.suggest('sid', path=None, latest='name the sourdough starter feature')
        self.assertEqual((name, source), ('Recipe · starter names', 'haiku'))
        self.assertIn('name the sourdough starter feature', self.seen[0])

    def test_no_model_falls_back_to_heuristic(self):
        nm.ask_claude = lambda prompt: None
        name, source = nm.suggest('sid', path=None, latest='fix the login redirect loop please')
        self.assertEqual(source, 'heuristic')
        self.assertIn('fix the login redirect', name)

    def test_nothing_to_go_on(self):
        self.assertEqual(nm.suggest('sid', path=None, latest='<command-name>/clear</command-name>'),
                         (None, 'no prompts yet'))
