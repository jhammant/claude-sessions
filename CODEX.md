# Codex sessions and transcript feed

The Codex companion lives in `codex/skill`. It preserves the macOS terminal
snapshot, restore, archive, page and layout workflow while keeping Claude's
state and commands separate.

```bash
mkdir -p "$HOME/.agents/skills" "$HOME/.local/bin"
ln -s "$PWD/codex/skill" "$HOME/.agents/skills/codex-sessions"
ln -s "$PWD/codex/skill/scripts/codex-sessions" "$HOME/.local/bin/codex-sessions"
codex-sessions save
codex-sessions restore --dry-run
codex-sessions find parser
codex-sessions page --all
```

Inspect existing destinations before linking. Start a new Codex thread and use
`$codex-sessions`. The executable requires Python 3.9+ and window operations
require macOS Automation permission for iTerm2/Terminal.

Desktop app threads can appear in the library, but this CLI only saves/restores
terminal windows; it does not manipulate the desktop app sidebar or layout.
Interactive processes are matched only by exact resume UUID or an open rollout
file descriptor. Unmatched processes are marked fresh, not guessed by mtime.
Window creation does not prove the agent completed its resume handshake.

## Shared history feed

`codex/skill/scripts/codex_transcripts.py` normalizes known Codex rollout message
records into a private, text-only compatibility feed under
`~/.codex/transcript-feed/projects`. It supports response items and event-only
fallbacks, excludes duplicate event mirrors and compaction replays, and skips
subagents, headless jobs, injected setup messages, reasoning, tools and images.
Known credential patterns are redacted; this is not a guarantee of exhaustive
secret detection. Never publish the feed or generated session pages.

`CODEX_HOME` selects the rollout root; `CODEX_TRANSCRIPT_FEED` overrides the
feed directory. `CODEX_SESSIONS_STATE` and `CODEX_SESSIONS_HISTORY` override
the separate snapshot files. Sources are never modified or deleted.

```bash
python3 codex/skill/scripts/codex_feed.py sync
```

Sync backfills searchable history only; it does not spend model quota or replay
historical learning evidence. Install the companion `continuous-learning`
`scripts/codex-stop.py` as a Codex Stop hook for future turn-by-turn updates.
It exports history, queues immutable non-overlapping learning batches, then
runs the existing configured extractor. Batches under 400 text characters wait
for more material; off mode discards observed evidence instead of deferring it.
Existing accepted rules remain manually approved.

Rollout schemas are internal, not a stable API. Unknown formats may be skipped;
the tests cover known records, not encrypted or unavailable conversation data.
Archived rollouts outside `sessions/` are not automatically backfilled.

## Tests

```bash
TEST_CL_SCRIPT=/path/to/continuous-learning/scripts/cl.py \
  python3 -m unittest discover -s codex/tests -v
```

The Codex window manager is derived from this repository's MIT-licensed Claude
manager. Changes to window-management behavior should be considered for both
implementations; provider discovery and resume syntax must remain separate.
