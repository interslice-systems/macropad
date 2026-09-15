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
# Field order: session, window_id, index, active, bell, name -- NOTE active
# (field 4) precedes bell (field 5). An earlier draft had them swapped, and
# the inversion was invisible in review because the fixture's all-zero third
# row can't distinguish the two positions; only a fixture with a bell but no
# active flag (or vice versa) catches it (see test_tmux.py). This inversion
# is what BLOCKED an earlier task, it is invisible to anyone editing the
# format string above without re-deriving it, and the failure mode -- a
# silent active/bell swap on the pad -- would ship quietly, not crash.
DECK_FORMAT = ("#{session_name}\t#{window_id}\t#{window_index}\t"
               "#{window_active}\t#{window_bell_flag}\t#{window_name}")


def parse_deck_windows(out):
    """Every window on the server as [{id, s, i, n, active, bell}]. Field
    order matches DECK_FORMAT above: session, window_id, index, active,
    bell, name."""
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 5)
        if len(parts) != 6:
            continue
        sess, wid, idx, active, bell, name = parts
        try:
            i = int(idx)
        except ValueError:
            continue
        items.append({"id": "tmux:" + wid, "s": sess, "i": i, "n": name,
                      "active": active == "1", "bell": bell == "1"})
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
