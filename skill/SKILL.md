---
name: claude-sessions
description: Save, restore, find and arrange Claude Code windows — which directories were open, which conversation each was on, where each window sat on screen — plus a searchable library of every past conversation. Use when the user says "save my sessions", "restore my claude windows", "reopen everything", "I need to restart, don't lose my sessions", "how do I get all my windows back", "resume all my conversations", "arrange my claude windows", "where is my <project> session", "find the window I was using for X", "which window is that in", "what was I doing in X", or asks how Claude Code session persistence works. Also use to set up an automatic periodic snapshot.
---

# claude-sessions — save, restore and arrange your open Claude Code windows

## What is and isn't already saved

Claude Code **already persists every conversation automatically** to:

```text
~/.claude/projects/<cwd-with-/-and-.-replaced-by-dashes>/<session-uuid>.jsonl
```

Nothing is lost on restart, and for a single window there is nothing to do:

- `claude -c` / `--continue` — continue the most recent conversation in this directory
- `claude -r` / `--resume` — interactive picker of past sessions in this directory
- `claude --resume <session-id>` — jump straight to a specific conversation

What **is** lost is the *window layout*: which directories were open, which
conversation each window was on, and where each window sat on screen. Someone
with fifteen windows across a dozen repositories cannot reconstruct that from a
picker. That is the gap this fills.

## Use it

```bash
claude-sessions save                # snapshot the open windows
claude-sessions list                # show the snapshot, with a title per session
claude-sessions restore             # reopen whatever is missing, where it used to sit
claude-sessions restore --dry-run   # print the commands, open nothing
claude-sessions restore --layout grid
claude-sessions arrange grid        # retile the windows that are open right now
claude-sessions find gloss          # which window is that in? is it even open?
claude-sessions find gloss --go     # bring it forward, or reopen it if it's closed
claude-sessions page --open         # searchable page of every conversation
claude-sessions doctor              # show exactly what it can and cannot see
```

`save` records, for every running Claude Code window: its working directory, its
session id, its on-screen position, and a title lifted from the conversation's
first user message so the list reads as something human.

`restore` **reconciles** — it reopens only the windows that are not already
open, so running it twice does not give you duplicates. Each window comes back
at its saved position unless you ask for a different `--layout`.

## Finding a conversation again

```bash
claude-sessions find gloss
claude-sessions find "rate limiter" --go
```

`find` searches the directory, branch, first message and last message of every
conversation — open or not. Open ones sort first and show the tty and screen
position they are on. `--go` acts on the top match: it brings that window
forward if it is running, and reopens it if it is not. This is the answer to
"where is my X session, and is it even open?".

## Remembering what you closed

A window you deliberately closed should be remembered but not reopened:

```bash
claude-sessions archive --gone         # everything not open right now
claude-sessions archive glossfm        # by directory fragment or session id
claude-sessions archive glossfm --undo
```

`restore` leaves archived conversations closed (`--include-archived` overrides).

```bash
claude-sessions page --open            # everything, in a browser
claude-sessions page --all --out ~/sessions.html
```

`page` writes a self-contained HTML file (default `~/.claude/sessions.html`)
listing every real conversation on disk: what you opened it with, **where you
left off**, the directory and branch, whether it is open / closed / archived,
and a click-to-copy `claude --resume` command. It is a plain file — regenerate
it whenever, nothing is uploaded.

Only `entrypoint: cli` transcripts are listed. Hooks and scripts write
`sdk-cli` / `sdk-py` transcripts into the same directories, and those are not
conversations you had.

## Two things it will not do to you

**It never shrinks the snapshot.** `save` merges: a window that is no longer
running is *remembered* (for `--keep-hours`, default 72) rather than deleted.
This matters because the failure that loses your work is a restore that only
half-succeeds followed by an automatic save that overwrites the good snapshot
with the smaller one. `save --replace` opts out when you really do want a clean
slate. Every snapshot is also copied to `~/.claude/session-restore.d/` (last 30),
so `list --history` and `restore --source <n>` can go back.

**It never fails silently.** If a window cannot be identified, positioned or
reopened, it says so and prints the exact command to retry.

## How it finds the sessions

`doctor` shows the mapping and how confident it is about each row:

- `resume` — exact. The session id is on the process command line (every window
  that was itself restored is in this state, so the mapping self-heals).
- `start` — strong. The transcript was created moments after that process began.
- `mtime` — a guess. Most recently written unclaimed transcript in that directory.
- `fresh` — the window has no conversation yet; it comes back as an empty `claude`.

A transcript that already existed *before* a process started is never matched to
it. That matters more than it sounds: hooks and other tooling run headless
`claude -p` jobs whose transcripts land in the same project directory, and a
naive "most recently modified" match hands your window someone else's
conversation.

Window discovery deliberately ignores headless `claude -p` processes, processes
with no controlling terminal, and the helper `claude` children a window forks —
all three otherwise inflate the count and shift every mapping in that directory.

## Arranging the windows

```bash
claude-sessions arrange grid                 # tile everything that is open
claude-sessions arrange grid --cols 4
claude-sessions arrange cascade
claude-sessions arrange saved                # back to the snapshot's positions
claude-sessions arrange grid --busy-first    # sessions working right now get the first cells
claude-sessions arrange grid --rect 0,0,2560,1440   # confine to one display
```

Without `--rect` it tiles inside the bounding box of the windows already on
screen, which keeps everything on the display you actually work on.

To make it automatic:

```bash
claude-sessions install-arrange grid --busy-first
claude-sessions uninstall-arrange
```

That re-tiles **only when a window opens or closes**, so a window you drag
somewhere on purpose stays where you put it.

Note that a freshly created terminal window is still being placed by the
terminal, so its position has to be applied after a short settle and applied
twice — a single `set bounds` gets the size but not the origin.

## Automating the snapshot

`save` has to be run before you quit, which is exactly the moment nobody
remembers. Offer to install a periodic snapshot:

```bash
claude-sessions install-auto     # launchd agent, snapshot every 10 min
claude-sessions uninstall-auto
```

It is read-only — it inspects processes and file timestamps and writes one JSON
file. Nothing is sent anywhere.

## When helping with this

- Run `doctor` first. It answers "why is this window missing / on the wrong
  conversation" faster than reading the snapshot.
- `restore` is safe to run when things are already open — it skips them. Prefer
  it to `--force`, which will happily give you two windows on one conversation.
- Still offer `--dry-run` when there are many windows; restoring fifteen
  terminals unannounced is unwelcome.
- If the user has restarted and lost the windows without a snapshot, they are
  not stuck: `~/.claude/projects/` still has everything. Sort each project
  directory by mtime and rebuild a plausible set with `claude --resume <id>`.
- A skill is enumerated when a Claude Code window **starts**. A window that was
  already open when the skill was installed cannot see it — that window has to
  be restarted (or the skill invoked by running the script directly).
