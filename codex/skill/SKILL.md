---
name: codex-sessions
description: Find, save, restore and arrange Codex terminal sessions, or browse locally recorded Codex conversations. Use for Codex session management, not Claude sessions.
---

# Codex sessions

Run `scripts/codex-sessions` relative to this skill's actual directory, using
Python 3 if the executable is not on PATH. Start with `find QUERY` for a
conversation, `list` for the saved window snapshot, or `doctor` for discovery.

- `save` merges open terminal windows into the snapshot; closed sessions remain
  remembered. `save --replace` deliberately discards the previous snapshot.
- `restore --dry-run` shows commands; `restore` opens missing iTerm2/Terminal
  windows using `codex resume UUID`. No permission bypass is added.
- `find QUERY --go` focuses or reopens the selected match; disambiguate when the
  user has not identified which of several similarly named sessions they mean.
- `page --all` creates a private local HTML library; `--open` opens it.
- `arrange grid|cascade|saved` moves terminal windows. `archive QUERY` excludes
  a remembered session from restoration without deleting its transcript.

Only exact resume IDs and open-rollout file handles establish a terminal's
session identity. An unidentified window is not evidence that a thread was
lost. Desktop app conversations are searchable, but the CLI does not control
the app's sidebar or window layout. Do not describe a successful AppleScript
window creation as proof that Codex finished resuming the conversation.

The transcript feed is local and redacted, and excludes headless/subagent jobs,
injected instructions, reasoning and tool output. Do not upload it. The
`codex-` prefix used in history/search IDs is removed for real resume commands.
