#!/usr/bin/env python3
"""namer — name Claude Code windows after what they are doing now, not what they started as.

Claude Code names a window after its folder ("dev-2f") or titles it from the first
prompt. Long sessions drift: the window that began as "what's running on this machine?"
is backfilling market data a day later. With a dozen open, nobody can tell them apart.

    namer.py hook                      UserPromptSubmit hook: applies a pending name,
                                       queues a fresh one every few prompts
    namer.py list                      open windows: name now, suggested, locked?
    namer.py suggest <session>         print a name for one session (asks a small model)
    namer.py apply [--yes]             suggest names for every open window and push them in
    namer.py set <session> <name...>   name one window now
    namer.py lock|unlock <session>     stop / restart automatic renaming for a session

A name is "<Project> · <current task>", at most 44 characters.

How a name reaches a window:
  * At the next prompt: the hook returns hookSpecificOutput.sessionTitle, which renames
    the session everywhere (prompt box, terminal title, /resume picker, peer name).
    Claude Code watches ~/.claude/settings.json, so windows already open pick the hook
    up too (a --settings file is not watched).
  * Right now: `set`/`apply` type `/rename <name>` into the window, but only when its
    input box is visibly empty and no dialog is showing. /rename runs even while the
    window is busy, without interrupting it. A window that is not safe to type into
    keeps the name queued for the hook.

A name you give a window yourself (/rename, --name) is never overwritten: the hook
sees a title it did not set and locks that session. `unlock` hands it back.
"""

import glob
import json
import mmap
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
STATE_DIR = os.environ.get("CLAUDE_NAMER_STATE", os.path.join(CLAUDE, "session-names"))
LIVE_DIR = os.path.join(CLAUDE, "sessions")
PROJ = os.path.join(CLAUDE, "projects")
LOG = os.path.join(STATE_DIR, "namer.log")

MAX_LEN = 44
REFRESH_PROMPTS = int(os.environ.get("CLAUDE_NAMER_REFRESH_PROMPTS", 5))      # look again after this many prompts...
REFRESH_SECS = int(os.environ.get("CLAUDE_NAMER_REFRESH_SECS", 15 * 60))       # ...and this long since the last look
SUGGEST_TIMEOUT = 90
SEP = " · "
NOT_PROJECTS = {HOME, os.path.join(HOME, "dev")}


# ---------------------------------------------------------------- state

def state_path(sid):
    return os.path.join(STATE_DIR, sid + ".json")


def load_state(sid):
    try:
        with open(state_path(sid)) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(sid, st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_path(sid) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, state_path(sid))


def log(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- transcripts

def transcript_of(sid):
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def last_custom_title(path):
    """The newest customTitle in a transcript ("last wins"), found without parsing it all."""
    try:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            end = len(mm)
            while True:
                i = mm.rfind(b'"custom-title"', 0, end)
                if i < 0:
                    return None
                start = mm.rfind(b"\n", 0, i) + 1
                stop = mm.find(b"\n", i)
                try:
                    d = json.loads(mm[start:stop if stop >= 0 else len(mm)].decode("utf-8", "replace"))
                    if d.get("type") == "custom-title":
                        return d.get("customTitle")
                except Exception:
                    pass                               # the words appeared inside some other record
                end = start
    except Exception:
        return None


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def is_prompt(text):
    """A request the person typed, not a tool result, command echo or compaction summary."""
    t = text.strip()
    return bool(t) and not t.startswith("<") and not t.startswith("This session is being continued") \
        and not t.startswith("[Request interrupted")


def clip_prompt(text, n=280):
    t = re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " [pasted text] ", text, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(t.split())[:n]


def recent(path, keep=6):
    """Latest prompts, the latest reply and the working directory from the end of a transcript.

    Reads a growing slice from the end: long autonomous runs can bury the last prompt
    under megabytes of tool output."""
    out = None
    for tail in (2_000_000, 16_000_000, None):
        out = _recent(path, tail, keep)
        if len(out["prompts"]) >= min(keep, 3) or tail is None or tail >= _size(path):
            break
    return out


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _recent(path, tail_bytes, keep):
    out = {"prompts": [], "reply": "", "cwd": None, "branch": None}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if tail_bytes and size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()                         # drop the partial first line
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        out["cwd"] = d.get("cwd") or out["cwd"]
        out["branch"] = d.get("gitBranch") or out["branch"]
        kind = d.get("type")
        if kind == "user" and not d.get("isMeta"):
            t = _text(d.get("message", {}).get("content"))
            if is_prompt(t):
                out["prompts"].append(clip_prompt(t))
        elif kind == "assistant":
            t = _text(d.get("message", {}).get("content")).strip()
            if t:
                out["reply"] = " ".join(t.split())[:500]
    out["prompts"] = out["prompts"][-keep:]
    return out


def project_of(cwd):
    """Repo name for a folder. A git worktree ("shop-wt/pr1") and a forkcode fork
    ("shop--passkeys") both count as the repo they came from."""
    if not cwd or os.path.realpath(cwd) in NOT_PROJECTS:
        return None
    try:
        top, common = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, timeout=5).stdout.split("\n")[:2]
    except Exception:
        top, common = "", ""
    if common.endswith("/.git"):
        top = os.path.dirname(common)                  # the main checkout, for a linked worktree
    if top and os.path.realpath(top) in NOT_PROJECTS:
        top = ""
    name = os.path.basename(top or cwd.rstrip("/"))
    return name.split("--")[0] or None


# ---------------------------------------------------------------- naming

PROMPT = """You name Claude Code terminal windows so their owner can tell a dozen apart at a glance.

Window facts:
- folder: {cwd}
- git repo: {project}  branch: {branch}
- current name: {current}
- the owner's latest requests, oldest first:
{prompts}
- the assistant's latest reply begins: {reply}

Name what this window is doing NOW, from the latest requests, not what it started as.
Format: <Project> · <task>
- Project: the product, repo or machine being worked on, one or two words, spelled the way the owner writes it (e.g. "Billing", "web-app", "homelab", "iOS app"). Use the repo name unless the latest requests are plainly about something else; then name that.
- task: two to five plain words for the concrete current job, e.g. "passkeys sign-in", "App Store go-live", "nightly backup fix". Prefer the goal over the step being debugged.
- Name the subject, never the process: not "continue from handoff", "resume work", "read FORK.md", "fix issues" or "next steps".
- At most {max_len} characters in total. No quotes, no emoji, no full stop.
If the current name already fits, reply with it unchanged.
Reply with the name and nothing else."""


def build_prompt(info, current):
    prompts = "\n".join("  %d. %s" % (i + 1, p) for i, p in enumerate(info["prompts"])) or "  (none yet)"
    return PROMPT.format(cwd=info.get("cwd") or "?", project=project_of(info.get("cwd")) or "none",
                         branch=info.get("branch") or "-", current=current or "none",
                         prompts=prompts, reply=info.get("reply") or "(none)", max_len=MAX_LEN)


def clean(raw):
    """One tidy name from whatever the model said, or None."""
    if not raw:
        return None
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    if not lines:
        return None
    name = re.sub(r"^(name|title)\s*:\s*", "", lines[-1] if len(lines[0]) > 80 else lines[0], flags=re.I)
    name = name.strip(" \t\"'`*.")
    name = re.sub(r"\s*[-–—|:]\s+", SEP, name, count=1) if SEP not in name else name
    name = " ".join(name.split())
    if len(name) > MAX_LEN:
        cut = name[:MAX_LEN + 1].rsplit(" ", 1)[0]
        name = cut.rstrip(" ·-")
    if not name or len(name) < 3:
        return None
    return name


def heuristic(info):
    """No model reachable: project plus the first words of the latest request."""
    proj = project_of(info.get("cwd")) or "session"
    words = (info["prompts"][-1] if info["prompts"] else "").split()[:5]
    return clean(proj + SEP + " ".join(words)) if words else proj


def ask_lmstudio(prompt, base="http://localhost:1234"):
    """Only if a model is already loaded: never make LM Studio load 20 GB for a title."""
    try:
        with urllib.request.urlopen(base + "/api/v0/models", timeout=2) as r:
            models = json.load(r).get("data", [])
        loaded = [m["id"] for m in models if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]
        if not loaded:
            return None
        body = json.dumps({"model": loaded[0], "temperature": 0.2, "max_tokens": 60,
                           "messages": [{"role": "user", "content": prompt + " /no_think"}]}).encode()
        req = urllib.request.Request(base + "/v1/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=SUGGEST_TIMEOUT) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except Exception:
        return None


def ask_claude(prompt):
    """A one-shot Haiku call with no hooks, no MCP and no transcript.

    The hook inherits the window's CLAUDE_* environment (session id, Remote Control
    bridge, messaging socket). Passed on, the child registers as a second interactive
    window of that session, so none of it is."""
    with tempfile.TemporaryDirectory() as d:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env.update(CLAUDE_NAMER_CHILD="1", PWD=d)
        try:
            p = subprocess.run(["claude", "-p", "--model", "haiku", "--no-session-persistence",
                                "--setting-sources", "project", "--strict-mcp-config",
                                "--disable-slash-commands", prompt],
                               cwd=d, env=env, capture_output=True, text=True,
                               timeout=SUGGEST_TIMEOUT, stdin=subprocess.DEVNULL)
            return p.stdout if p.returncode == 0 else None
        except Exception:
            return None


def suggest(sid, path=None, current=None, latest=None):
    """latest: the prompt being submitted right now. The hook runs before Claude Code
    writes it to the transcript, so on a first prompt the transcript has nothing yet."""
    path = path or transcript_of(sid)
    info = recent(path) if path else {"prompts": [], "reply": "", "cwd": None, "branch": None}
    if latest and is_prompt(latest):
        latest = clip_prompt(latest)
        if not info["prompts"] or info["prompts"][-1] != latest:
            info["prompts"] = (info["prompts"] + [latest])[-6:]
    if not info["prompts"]:
        return None, "no prompts yet"
    prompt = build_prompt(info, current)
    for source, fn in (("lmstudio", ask_lmstudio), ("haiku", ask_claude)):
        name = clean(fn(prompt))
        if name:
            return name, source
    return heuristic(info), "heuristic"


# ---------------------------------------------------------------- the hook

def decide(st, hook_input, custom_title, now):
    """Pure core of the hook. Returns (title_to_emit_or_None, new_state, refresh_due)."""
    st = dict(st)
    st["prompts"] = st.get("prompts", 0) + 1
    shown = hook_input.get("session_title")

    # A title we did not set, which is the window's own custom title, came from a person
    # (/rename or --name). Respect it from now on.
    if not st.get("locked") and shown and shown != st.get("applied") and shown == custom_title:
        st.update(locked=True, applied=shown, locked_reason="named by hand")
        st.pop("pending", None)

    emit = None
    pending = st.pop("pending", None)
    if pending and not st.get("locked") and pending != shown:
        emit = pending
        st["applied"] = pending
    elif pending and pending == shown:
        st["applied"] = pending

    if "suggesting_since" in st and now - st["suggesting_since"] > 2 * SUGGEST_TIMEOUT:
        st.pop("suggesting_since")                    # a stuck suggester never blocks for good
    due = False
    if not st.get("locked") and "suggesting_since" not in st:
        last_p, last_t = st.get("named_prompt"), st.get("named_at", 0)
        due = last_p is None or (st["prompts"] - last_p >= REFRESH_PROMPTS and now - last_t >= REFRESH_SECS)
    return emit, st, due


def cmd_hook(_args):
    if os.environ.get("CLAUDE_NAMER_CHILD") or os.environ.get("CLAUDE_NAMER_OFF"):
        return 0
    try:
        hook_input = json.load(sys.stdin)
        sid = hook_input.get("session_id")
        path = hook_input.get("transcript_path")
        if not sid or hook_input.get("hook_event_name") != "UserPromptSubmit":
            return 0
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "cli") not in ("cli", ""):
            return 0                                   # headless runs are not windows
        now = time.time()
        emit, st, due = decide(load_state(sid), hook_input, last_custom_title(path) if path else None, now)
        if due:
            st["suggesting_since"] = now
        save_state(sid, st)
        if due:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "suggest", sid, "--store",
                              "--transcript", path or "", "--current", hook_input.get("session_title") or "",
                              "--latest", (hook_input.get("prompt") or "")[:2000]],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        if emit:
            log("%s hook applies %r" % (sid[:8], emit))
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": emit}}))
    except Exception as e:                             # a naming hiccup must never block a prompt
        log("hook error: %r" % e)
    return 0


# ---------------------------------------------------------------- open windows

def live_windows():
    rows = []
    for f in glob.glob(os.path.join(LIVE_DIR, "*.json")):
        try:
            d = json.load(open(f))
            pid = int(d["pid"])
            os.kill(pid, 0)
        except Exception:
            continue
        if d.get("kind") != "interactive" or d.get("entrypoint", "cli") != "cli":
            continue                                   # headless runs (sdk-cli) register too
        tty = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        d["tty"] = "/dev/" + tty if tty and tty not in ("??", "-") else None
        rows.append(d)
    return sorted(rows, key=lambda d: d.get("startedAt", 0))


def find_window(target, rows=None):
    rows = live_windows() if rows is None else rows
    hits = [r for r in rows if r["sessionId"].startswith(target) or r.get("name") == target]
    return hits[0] if len(hits) == 1 else None


SCREEN_AS = """
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "%s" then return contents of s
      end repeat
    end repeat
  end repeat
  return "NO-SUCH-TTY"
end tell
"""

TYPE_AS = """
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "%s" then
          tell s to write text "%s"
          return "ok"
        end if
      end repeat
    end repeat
  end repeat
  return "NO-SUCH-TTY"
end tell
"""

DIALOG_MARKERS = ("Enter to select", "Esc to cancel", "Do you want to", "↑/↓ to", "Tab to amend")


def input_is_empty(screen):
    """True when the bottom input box is empty and no dialog or menu is showing.

    The box is a lone line starting with ❯ between two ──── rules, at the bottom of the
    screen. An open dialog replaces the box, so a half-typed prompt, a permission dialog
    ("❯ 1. Yes") or a question menu all fail; a menu already answered, still in the
    scrollback above the box, does not."""
    lines = [l.replace("\xa0", " ").rstrip() for l in screen.rstrip("\n").split("\n")]
    tail = lines[-12:]
    for i in range(len(tail) - 1, 0, -1):
        if tail[i].lstrip().startswith("❯"):
            above, below = tail[i - 1].lstrip(), tail[i + 1].lstrip() if i + 1 < len(tail) else ""
            if not (above.startswith("─") and below.startswith("─")):
                return False
            if any(m in l for l in tail[i - 1:] for m in DIALOG_MARKERS):
                return False
            typed = tail[i].lstrip()[1:].strip()
            return typed == "" or typed.startswith('Try "')
    return False


def osa(script, timeout=20):
    try:
        p = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""


def type_rename(tty, name):
    """Type /rename into an iTerm2 window, only if it is safe. Returns (ok, why)."""
    if not tty:
        return False, "no terminal"
    screen = osa(SCREEN_AS % tty)
    if not screen or screen == "NO-SUCH-TTY":
        return False, "no iTerm2 tab on " + tty
    if not input_is_empty(screen):
        return False, "input box not empty, or a dialog is open"
    safe = name.replace("\\", "").replace('"', "'")
    return (osa(TYPE_AS % (tty, "/rename " + safe)) == "ok"), "typed"


def name_window(row, name, by="tool"):
    sid = row["sessionId"]
    st = load_state(sid)
    st.update(applied=name, named_at=time.time(), named_prompt=st.get("prompts", 0), by=by)
    st.pop("pending", None)
    if by == "hand":
        st.update(locked=True, locked_reason="named with namer set")
    save_state(sid, st)                 # before typing, so the hook knows this name is ours
    ok, why = type_rename(row.get("tty"), name)
    if not ok:
        st["pending"] = name            # the hook applies it at the next prompt, if loaded
        save_state(sid, st)
    log("%s %s %r (%s)" % (sid[:8], "renamed" if ok else "queued", name, why))
    return ok, why


# ---------------------------------------------------------------- commands

def cmd_suggest(args):
    sid = args.session
    row = find_window(sid) if not args.transcript else None
    if row:
        sid = row["sessionId"]
    current = args.current or (row or {}).get("name")
    name, source = suggest(sid, args.transcript or None, current, args.latest or None)
    if args.store:
        st = load_state(sid)
        st.pop("suggesting_since", None)
        st["last_source"] = source
        if name:                                      # no name: try again at the next prompt
            st.update(named_at=time.time(), named_prompt=st.get("prompts", 0))
            if name != current and not st.get("locked"):
                st["pending"] = name
        save_state(sid, st)
        log("%s suggested %r via %s" % (sid[:8], name, source))
    else:
        print(name or "(no name: %s)" % source)
    return 0


def cmd_list(_args):
    for r in live_windows():
        st = load_state(r["sessionId"])
        flag = "locked" if st.get("locked") else ("pending: " + st["pending"]) if st.get("pending") else ""
        print("%-10s %-44s %-8s %s" % (r["sessionId"][:8], r.get("name", "?")[:44], r.get("status", ""), flag))
    return 0


def cmd_set(args):
    row = find_window(args.session)
    if not row:
        print("no single open window matches %r" % args.session)
        return 1
    name = clean(" ".join(args.name))
    ok, why = name_window(row, name, by="hand" if args.lock else "tool")
    print("%s  %s -> %s  (%s)" % (row["sessionId"][:8], row.get("name"), name, why if ok else why + "; queued"))
    return 0 if ok else 2


def cmd_apply(args):
    rows = [r for r in live_windows() if r["sessionId"] != os.environ.get("CLAUDE_SESSION_ID")]
    plan = []
    for r in rows:
        st = load_state(r["sessionId"])
        if st.get("locked"):
            print("%-10s %-30s locked, left alone" % (r["sessionId"][:8], r.get("name")))
            continue
        name, source = suggest(r["sessionId"], current=r.get("name"))
        if name and name != r.get("name"):
            plan.append((r, name))
            print("%-10s %-30s -> %s  [%s]" % (r["sessionId"][:8], r.get("name"), name, source))
    if not args.yes:
        print("\n(dry run: add --yes to rename)")
        return 0
    for r, name in plan:
        ok, why = name_window(r, name)
        print("%-10s %s" % (r["sessionId"][:8], "renamed" if ok else "queued: " + why))
    return 0


def cmd_lock(args, locked=True):
    row = find_window(args.session)
    sid = row["sessionId"] if row else args.session
    st = load_state(sid)
    st["locked"] = locked
    if not locked:
        st.pop("locked_reason", None)
        st.pop("named_prompt", None)                  # look again at the next prompt
    save_state(sid, st)
    print("%s %s" % (sid[:8], "locked" if locked else "unlocked"))
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="namer", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook").set_defaults(fn=cmd_hook)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("suggest")
    s.add_argument("session")
    s.add_argument("--transcript", default="")
    s.add_argument("--current", default="")
    s.add_argument("--latest", default="", help="the prompt being submitted now")
    s.add_argument("--store", action="store_true", help="queue the name for the hook instead of printing it")
    s.set_defaults(fn=cmd_suggest)
    s = sub.add_parser("set")
    s.add_argument("session")
    s.add_argument("name", nargs="+")
    s.add_argument("--lock", action="store_true", help="never auto-rename this one again")
    s.set_defaults(fn=cmd_set)
    s = sub.add_parser("apply")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("lock")
    s.add_argument("session")
    s.set_defaults(fn=lambda a: cmd_lock(a, True))
    s = sub.add_parser("unlock")
    s.add_argument("session")
    s.set_defaults(fn=lambda a: cmd_lock(a, False))
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
