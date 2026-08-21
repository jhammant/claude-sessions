---
name: claude-sessions
description: Save and restore the whole set of open Claude Code windows — which directories were open and which conversation each was on — so a machine restart doesn't cost you your working context. Use when the user says "save my sessions", "restore my claude windows", "reopen everything", "I need to restart, don't lose my sessions", "how do I get all my windows back", "resume all my conversations", or asks how Claude Code session persistence works. Also use to set up an automatic periodic snapshot.
---

# claude-sessions — save and restore your open Claude Code windows

## What is and isn't already saved

Claude Code **already persists every conversation automatically** to:

```
~/.claude/projects/<cwd-with-/-and-.-replaced-by-dashes>/<session-uuid>.jsonl
```

Nothing is lost on restart, and for a single window there is nothing to do:

- `claude -c` / `--continue` — continue the most recent conversation in this directory
- `claude -r` / `--resume` — interactive picker of past sessions in this directory
- `claude --resume <session-id>` — jump straight to a specific conversation

What **is** lost is the *window layout*: which directories were open and which
conversation each window was on. Someone with fifteen windows across a dozen
repositories cannot reconstruct that from a picker. That is the gap this fills.

## Use it

```bash
claude-sessions save              # snapshot the open windows
claude-sessions list              # show the snapshot, with a title per session
claude-sessions restore           # reopen each as its own window
claude-sessions restore --tabs    # reopen as tabs in a single window
claude-sessions restore --dry-run # print the commands, open nothing
```

`save` records, for every running Claude Code window: its working directory, its
session id, and a title lifted from the conversation's first user message so the
list reads as something human.

`restore` emits `cd <dir> && claude --resume <session-id>` per window, driving
iTerm2 if it is running and Terminal otherwise.

The snapshot lives at `~/.claude/session-restore.json` (override with
`CLAUDE_SESSIONS_STATE`). It is plain JSON — edit it to prune windows you don't
want back.

## How it finds the sessions, and the one soft spot

`pgrep -x claude` finds the windows; `lsof -a -p <pid> -d cwd` gives each one's
working directory; the directory maps into `~/.claude/projects/` by replacing
`/` and `.` with `-`.

**Claude Code does not hold the transcript file open**, so there is no exact
process-to-session handle. Where several windows share one directory, the tool
takes the *N most recently modified* sessions for that directory. Since all N
are live and being written to, this is reliable in practice — but it is a
heuristic, and two windows in the same directory can come back swapped. Say so
rather than implying the mapping is exact.

## Automating the snapshot

`save` has to be run before you quit, which is exactly the moment nobody
remembers. Offer to install a periodic snapshot:

```bash
scripts/claude-sessions install-auto     # launchd agent, snapshot every 10 min
scripts/claude-sessions uninstall-auto
```

It is read-only — it inspects processes and file timestamps and writes one JSON
file. Nothing is sent anywhere.

## When helping with this

- Check whether a snapshot already exists (`claude-sessions list`) before saving
  over it; a stale snapshot is better than none if the windows have already gone.
- If the user has already restarted and lost the windows without a snapshot, they
  are not stuck: `~/.claude/projects/` still has everything. Sort each project
  directory by mtime and rebuild a plausible set with `claude --resume <id>`.
- Always offer `--dry-run` first when there are many windows — restoring fifteen
  terminals unannounced is unwelcome.
