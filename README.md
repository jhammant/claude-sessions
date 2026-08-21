# claude-sessions

Save and restore the whole set of open Claude Code windows — which directories
were open, which conversation each one was on, and where each window sat on
screen — so restarting your machine doesn't cost you your working context.

Ships as a **Claude Code skill** and a standalone CLI.

## The problem

Claude Code already persists every conversation automatically:

```text
~/.claude/projects/<cwd-with-/-and-.-as-dashes>/<session-uuid>.jsonl
```

So nothing is lost on restart, and for one window there's nothing to do —
`claude -c` continues the last conversation here, `claude -r` opens a picker.

But if you work with a dozen windows across a dozen repositories, the *layout*
is gone: you can't remember which conversation each window was on, and a picker
per directory won't tell you.

## Use

```bash
claude-sessions save              # snapshot the open windows
claude-sessions list              # show it, with a title per session
claude-sessions restore           # reopen what's missing, where it used to sit
claude-sessions restore --dry-run # print the commands, open nothing
claude-sessions restore --tabs    # ...as tabs in one window
claude-sessions find gloss        # which window is that in? is it even open?
claude-sessions find gloss --go   # bring it forward, or reopen it if it's closed
claude-sessions page --open       # searchable page of every conversation
claude-sessions doctor            # show exactly what it can and cannot see

claude-sessions install-auto      # snapshot every 10 min via launchd
claude-sessions uninstall-auto
```

`list` reads like something human, because each entry carries a title taken from
the conversation's first message:

```text
3 window(s), snapshot taken 2026-03-04 18:22:
  /Users/you/dev/api-gateway   [open now]
     3f2a9c14  rate limiter is dropping requests under load
  /Users/you/dev/mobile-app
     7d4e1b90  migrate the onboarding flow off the old SDK
  /Users/you/dev/infra   [remembered]
     b81c5f37  terraform plan wants to replace the whole cluster
```

*(illustrative — the real thing shows your own directories and conversations.)*

## Finding a conversation again

```bash
$ claude-sessions find gloss
2 match(es) for 'gloss':
  OPEN ~/dev  [ttys033, at 792,729]
        how can i get glossfm ready to launch, even if just a small test
        left off: could we monetise the free login with ads?
       ~/dev/glossfm
        Podcast player built for learning
        cd ~/dev/glossfm && claude --resume 8f84baff-...
```

It searches directory, branch, first message and last message across every
conversation, open or not. `--go` brings the top match's window forward if it's
running, and reopens it if it isn't.

## Remembering what you closed

A window you closed on purpose should be remembered, not reopened:

```bash
claude-sessions archive --gone      # everything not open right now
claude-sessions archive glossfm     # or by directory / session id
claude-sessions page --open         # browse the lot
```

`page` writes a self-contained HTML file (`~/.claude/sessions.html`) listing
every conversation: what you opened it with, **where you left off**, directory
and branch, whether it's open / closed / archived, and a click-to-copy
`claude --resume` command. Nothing is uploaded — it's a local file.

## Arranging windows

```bash
claude-sessions arrange grid                 # tile everything that's open
claude-sessions arrange grid --cols 4
claude-sessions arrange cascade
claude-sessions arrange saved                # back to the snapshot's positions
claude-sessions arrange grid --busy-first    # sessions working right now get the first cells
claude-sessions arrange grid --rect 0,0,2560,1440   # confine to one display

claude-sessions install-arrange grid --busy-first   # do it automatically
claude-sessions uninstall-arrange
```

`restore` also takes `--layout grid|cascade|saved|none`. The automatic mode
re-tiles **only when a window opens or closes**, so a window you drag somewhere
on purpose stays where you put it.

## Install

```bash
git clone https://github.com/jhammant/claude-sessions ~/dev/claude-sessions
ln -s ~/dev/claude-sessions/skill ~/.claude/skills/claude-sessions          # as a skill
ln -s ~/dev/claude-sessions/skill/scripts/claude-sessions ~/.local/bin/     # as a CLI
```

A skill is enumerated when a Claude Code window *starts*, so windows that were
already open won't see it until they're restarted.

## How it works

`ps` finds the windows, `lsof` gives each one's working directory, and the
directory maps into `~/.claude/projects/` by replacing `/` and `.` with `-`.
Window positions come from iTerm2/Terminal via the tty each process is on.

Claude Code doesn't hold its transcript file open, so there's no direct
process-to-session handle. Instead, each window is matched in three passes, and
`doctor` tells you which one applied:

| how | confidence | when |
| --- | --- | --- |
| `resume` | exact | the session id is on the process command line |
| `start` | strong | the transcript was created moments after the process began |
| `mtime` | a guess | most recently written unclaimed transcript in that directory |
| `fresh` | n/a | the window has no conversation yet |

Because a restored window carries `--resume <id>` on its command line, the
mapping is exact from the first restore onward — it self-heals.

A transcript that already existed *before* a process started is never matched to
it. That matters more than it sounds: hooks and other tooling run headless
`claude -p` jobs whose transcripts land in the same project directory, and a
naive "most recently modified" match will hand your window someone else's
conversation. For the same reason, discovery ignores headless processes,
processes with no controlling terminal, and the helper `claude` children that a
window forks.

## Two things it won't do to you

**It never shrinks the snapshot.** `save` merges: a window that is no longer
running is remembered (for `--keep-hours`, default 72) rather than deleted. The
failure mode that actually loses your work is a restore that half-succeeds
followed by an automatic save overwriting the good snapshot with the smaller
one. `save --replace` opts out. Every snapshot is also copied to
`~/.claude/session-restore.d/` (last 30) — `list --history` and
`restore --source <n>` go back.

**It never fails silently.** `restore` reconciles against what's already open
(so running it twice doesn't give you duplicates), verifies each window it
creates, retries, and prints the exact command for anything it couldn't reopen.

## Scope

macOS. Drives iTerm2 if it's running, Terminal otherwise. Read-only apart from
one JSON file at `~/.claude/session-restore.json` (override with
`CLAUDE_SESSIONS_STATE`); nothing is sent anywhere.

## Licence

MIT.
