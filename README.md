# claude-sessions

Save and restore the whole set of open Claude Code windows — which directories
were open and which conversation each one was on — so restarting your machine
doesn't cost you your working context.

Ships as a **Claude Code skill** and a standalone shell script.

## The problem

Claude Code already persists every conversation automatically:

```
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
claude-sessions restore           # reopen each as its own window
claude-sessions restore --tabs    # ...as tabs in one window
claude-sessions restore --dry-run # print the commands, open nothing

claude-sessions install-auto      # snapshot every 10 min via launchd
claude-sessions uninstall-auto
```

`list` reads like something human, because each entry carries a title taken from
the conversation's first message:

```
3 session(s):
  /Users/you/dev/api-gateway
     3f2a9c14-0b7e-4d51-9a63-8c1d0e5f7b22  rate limiter is dropping requests under load
  /Users/you/dev/mobile-app
     7d4e1b90-6c2f-4a83-b715-2e9f0a3c8d61  migrate the onboarding flow off the old SDK
  /Users/you/dev/infra
     b81c5f37-9a04-42de-8f16-0d7b3e6a2c45  terraform plan wants to replace the whole cluster
```

*(illustrative — the real thing shows your own directories and conversations.)*

## Install

```bash
git clone https://github.com/jhammant/claude-sessions ~/dev/claude-sessions
ln -s ~/dev/claude-sessions/skill ~/.claude/skills/claude-sessions   # as a skill
ln -s ~/dev/claude-sessions/skill/scripts/claude-sessions ~/.local/bin/   # as a CLI
```

## How it works, and where it's approximate

`pgrep -x claude` finds the windows, `lsof` gives each one's working directory,
and the directory maps into `~/.claude/projects/` by replacing `/` and `.` with
`-`.

**Claude Code doesn't hold the transcript file open**, so there's no exact
process-to-session handle. Where several windows share a directory, the tool
takes the *N most recently modified* sessions for that directory. All N are live
and being written to, so this is reliable in practice — but it's a heuristic,
and two windows in the same directory can come back swapped.

## Scope

macOS. Drives iTerm2 if it's running, Terminal otherwise. Read-only apart from
one JSON file at `~/.claude/session-restore.json` (override with
`CLAUDE_SESSIONS_STATE`); nothing is sent anywhere.

## Licence

MIT.
