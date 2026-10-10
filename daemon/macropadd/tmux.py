"""tmux and local-client CLI wrappers."""
import asyncio
import json
from pathlib import Path


LOCAL_CLIENTS_COMMAND = Path.home() / ".local/bin/tmux-local-clients"


async def _run(*args):
    proc = await asyncio.create_subprocess_exec(
        "tmux", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


async def _run_local_clients():
    proc = await asyncio.create_subprocess_exec(
        str(LOCAL_CLIENTS_COMMAND), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


def _plain_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_local_clients(out):
    """Parse and validate a tmux-local-clients JSON snapshot."""
    data = json.loads(out)
    if not isinstance(data, list):
        raise ValueError("local client snapshot is not an array")
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("local client record is not an object")
        workspace = item.get("workspace")
        if (not isinstance(item.get("session"), str) or not item["session"]
                or not isinstance(item.get("address"), str) or not item["address"]
                or not _plain_int(item.get("pid")) or item["pid"] <= 0
                or not isinstance(workspace, dict)
                or not _plain_int(workspace.get("id"))
                or not isinstance(workspace.get("name"), str)
                or not isinstance(item.get("class"), str)
                or not _plain_int(item.get("focusHistoryID"))
                or item["focusHistoryID"] < 0):
            raise ValueError("invalid local client record")
    return data


async def list_local_clients(run=_run_local_clients):
    """Return validated local terminal associations, or None on failure."""
    try:
        rc, out = await run()
        if rc != 0:
            return None
        return parse_local_clients(out)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


async def select_window(session, i, run=_run):
    """Select window i of session (exact-name match). True on success."""
    try:
        rc, _ = await run("select-window", "-t", "=%s:%d" % (session, i))
    except OSError:
        return False
    return rc == 0


# The deck keys off #{window_id} (@7), NOT session:index. Indices move under
# move-window, swap-window and renumber-windows, any of which would silently
# reshuffle every key and defeat sticky allocation in the way hardest to notice.
#
# Field order: session, window_id, index, active, bell, panes, name -- NOTE active
# (field 4) precedes bell (field 5). An earlier draft had them swapped, and
# the inversion was invisible in review because the fixture's all-zero third
# row can't distinguish the two positions; only a fixture with a bell but no
# active flag (or vice versa) catches it (see test_tmux.py). This inversion
# is what BLOCKED an earlier task, it is invisible to anyone editing the
# format string above without re-deriving it, and the failure mode -- a
# silent active/bell swap on the pad -- would ship quietly, not crash.
#
# `panes` (2026-10-10) is every pane's `command=@claude_state;`, so a window can
# say "a Claude in here is blocked on Chris's answer" -- the pad's persistent
# breathe. ~/oracle/scripts/claude-attn writes that pane option from Claude
# Code's hooks. The name stays LAST: it is the one free-text field, and the
# maxsplit parse below lets it keep a tab without shifting the others.
DECK_FORMAT = ("#{session_name}\t#{window_id}\t#{window_index}\t"
               "#{window_active}\t#{window_bell_flag}\t"
               "#{P:#{pane_current_command}=#{@claude_state};}\t#{window_name}")

# A pane back at a shell prompt cannot be blocked. A Claude that dies without
# firing SessionEnd leaves its last `blocked` on the pane, and without this
# guard that stale value would breathe forever.
SHELLS = frozenset(("bash", "sh", "zsh", "fish", "dash"))


def _panes_blocked(field):
    """True if any pane in `cmd=state;cmd=state;` is a live non-shell process
    whose @claude_state is blocked. rpartition: the state never holds '='."""
    for entry in field.split(";"):
        cmd, sep, state = entry.rpartition("=")
        if sep and state == "blocked" and cmd not in SHELLS:
            return True
    return False


def parse_deck_windows(out):
    """Every window on the server as [{id, s, i, n, active, bell, blocked}].
    Field order matches DECK_FORMAT above: session, window_id, index, active,
    bell, panes, name."""
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 6)
        if len(parts) != 7:
            continue
        sess, wid, idx, active, bell, panes, name = parts
        try:
            i = int(idx)
        except ValueError:
            continue
        items.append({"id": "tmux:" + wid, "s": sess, "i": i, "n": name,
                      "active": active == "1", "bell": bell == "1",
                      "blocked": _panes_blocked(panes)})
    return items


async def list_deck_windows(run=_run):
    """Every window across ALL sessions, or None on failure."""
    try:
        rc, out = await run("list-windows", "-a", "-F", DECK_FORMAT)
    except OSError:
        return None
    if rc != 0:
        return None
    return parse_deck_windows(out)
