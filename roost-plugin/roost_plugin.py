#!/usr/bin/env python3
"""Roost menu for claude-sessions: tile, focus and restore the Claude Code windows from the menu bar.

Every refresh is cheap on purpose (ps, one JXA call for the displays, the snapshot file): the
full window discovery takes seconds, so it only runs when you click something.
"""
import json
import os
import plistlib
import subprocess
import time
from pathlib import Path

HOME = Path.home()
TOOL = Path(__file__).resolve().parent.parent / "skill/scripts/claude-sessions"
SNAPSHOT = HOME / ".claude/session-restore.json"
ARRANGE_PLIST = HOME / "Library/LaunchAgents/dev.claudesessions.arrange.plist"
SNAPSHOT_PLIST = HOME / "Library/LaunchAgents/dev.claudesessions.snapshot.plist"
# One-press shortcuts Roost registers system-wide; shown beside the items in the menu.
HOTKEYS = {"smart": "ctrl+opt+cmd+s", "grid": "ctrl+opt+cmd+g", "focus": "ctrl+opt+cmd+f"}
LAYOUTS = [("smart", "Smart: the windows you used last get the most room"),
           ("grid", "Grid, busiest first"),
           ("focus", "Focus: busiest big, the rest beside it"),
           ("columns", "Side by side"),
           ("cascade", "Cascade")]
SEPARATOR = {"separator": True}
TITLE = "Organise Claude windows"


def cs(*args):
    """Run through the script's own shebang, so Roost uses the same Python as the terminal."""
    return [str(TOOL), *args]


def run(argv, timeout=10):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def open_windows():
    """Terminals running an interactive `claude`: one per tty, skipping headless `claude -p` jobs."""
    ttys = set()
    for line in run(["ps", "-axo", "tty=,args="]).splitlines():
        tty, _, args = line.strip().partition(" ")
        argv = args.split()
        if tty in ("", "??") or not argv or os.path.basename(argv[0]) != "claude":
            continue
        if "-p" in argv or "--print" in argv:
            continue
        ttys.add(tty)
    return len(ttys)


def displays():
    try:
        return json.loads(run(cs("displays", "--json")) or "[]")
    except ValueError:
        return []


def auto_tile_mode():
    """The layout the auto-tile agent applies, or None when it is off."""
    try:
        with open(ARRANGE_PLIST, "rb") as fh:
            argv = plistlib.load(fh).get("ProgramArguments", [])
    except (OSError, plistlib.InvalidFileException):
        return None
    return argv[argv.index("arrange") + 1] if "arrange" in argv[:-1] else "grid"


def closed_windows(limit=12):
    """Windows the snapshot remembers that were not open at its last save, newest first."""
    try:
        saved = json.loads(SNAPSHOT.read_text()).get("saved", [])
    except (OSError, ValueError):
        return []
    rows = [r for r in saved if not r.get("live") and not r.get("archived") and r.get("session")]
    rows.sort(key=lambda r: -(r.get("last_seen") or 0))
    return rows[:limit] if limit else rows


def ago(ts):
    minutes = max(0, int((time.time() - (ts or 0)) / 60))
    if minutes < 60:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago" if minutes < 1440 else f"{minutes // 1440}d ago"


def layout_items(windows):
    """The layouts, one click each, applied to every open window."""
    return [{"title": title, "run": cs("arrange", mode, "--busy-first"),
             **({"hotkey": HOTKEYS[mode]} if mode in HOTKEYS else {})}
            for mode, title in LAYOUTS if mode != "columns" or windows <= 6]


def more_layouts(screens):
    items = [{"title": f"Grid in {n} columns", "run": cs("arrange", "grid", "--busy-first", "--cols", str(n))}
             for n in (2, 3, 4, 5)]
    if len(screens) > 1:
        items.append(SEPARATOR)
        for i, screen in enumerate(screens, 1):
            label = screen["name"] + (" (main)" if i == 1 else "")
            items.append({"title": f"Only on {label}", "items": [
                {"title": title, "run": cs("arrange", mode, "--busy-first", "--display", str(i))}
                for mode, title in LAYOUTS if mode != "cascade"]})
    items += [SEPARATOR, {"title": "Back to saved positions", "run": cs("arrange", "saved"),
                          "tooltip": "Where each window sat at the last snapshot"}]
    return items


def auto_tile_items():
    current = auto_tile_mode()
    items = [{"title": "Off", "checked": current is None, "run": cs("uninstall-arrange")}]
    for mode, title in LAYOUTS:
        if mode == "cascade":
            continue
        items.append({"title": title, "checked": current == mode,
                      "run": cs("install-arrange", mode, "--busy-first")})
    return items


def reopen_items():
    everything = closed_windows(limit=0)
    if not everything:
        return [{"title": "Nothing closed since the last snapshot"}]
    items = []
    for row in everything[:12]:
        name = (row.get("title") or "").strip() or os.path.basename(row["cwd"].rstrip("/"))
        items.append({"title": f"{os.path.basename(row['cwd'].rstrip('/'))} — {name}"[:80],
                      "tooltip": f"{row['cwd']} · last open {ago(row.get('last_seen'))}",
                      "run": cs("find", row["session"], "--go")})
    items.append({"title": f"Reopen all {len(everything)}…", "run": cs("restore"),
                  "confirm": f"Reopen {len(everything)} Claude Code windows, each where it last sat?"})
    return items


def main():
    if not TOOL.exists():
        print(json.dumps({"title": TITLE, "items": [{"title": f"claude-sessions not found at {TOOL}"}]}))
        return
    windows, screens = open_windows(), displays()
    auto = auto_tile_mode()
    # pgrep can miss iTerm2 (it matched only iTerm's shell launchers here); ps lists the app itself
    terminal = "iTerm2" if "/iTerm2\n" in run(["ps", "-axo", "comm="]) + "\n" else "Terminal"
    items = [
        {"title": f"{windows} window{'s' if windows != 1 else ''} open"
                  + (f" · auto-tile: {auto}" if auto else "")},
        *layout_items(windows),
        {"title": "More layouts", "items": more_layouts(screens)},
        SEPARATOR,
        {"title": "Auto-tile as windows open and close", "items": auto_tile_items()},
        {"title": "Reopen a closed window", "items": reopen_items()},
        {"title": "Save layout now", "run": cs("save")},
        {"title": "Auto-save layout every 10 min", "checked": SNAPSHOT_PLIST.exists(),
         "run": cs("uninstall-auto") if SNAPSHOT_PLIST.exists() else cs("install-auto")},
        SEPARATOR,
        {"title": f"Bring every {terminal} window forward",
         "run": ["osascript", "-e", f'tell application "{terminal}" to activate']},
        {"title": "Browse every conversation…", "run": cs("page", "--open", "--fast"),
         "tooltip": "A searchable page of every Claude Code conversation, open or closed"},
    ]
    print(json.dumps({"title": TITLE, "items": items}))


if __name__ == "__main__":
    main()
