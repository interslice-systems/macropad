"""Hyprland IPC: instance discovery, one-shot requests, event stream state."""
import asyncio
import json
import unicodedata
from pathlib import Path

# colorhash replaced the cksum%7 / Okabe-Ito system on 2026-08-21. The palette is
# DATA now — the same ~/.config/colorhash/palette.json the bash side reads
# (~/oracle/scripts/workspace-identity-lib). Pad and bar agree because they read one
# file, not because two hardcoded lists were kept in sync by hand.
#
# We take the `led` surface specifically. palette.json carries five renderings per
# cell and they are NOT interchangeable: `bg` is Petroff's published fill, `lightFg`
# and `darkFg` are re-lightened for text on a theme ground, and `led` is the one
# tuned for WS2812s. Using `bg` here would send a color that was never checked
# against the pad's LEDs.
PALETTE_FILE = Path.home() / ".config" / "colorhash" / "palette.json"

_LED_CACHE = None


def led_palette(path=None):
    """The `led` hex column of palette.json, or [] when it is absent/malformed.

    Empty means NO colors get sent, never a fallback set: a wrong-but-plausible
    palette on the pad is harder to notice than an unlit one.
    """
    global _LED_CACHE
    if path is None and _LED_CACHE is not None:
        return _LED_CACHE
    try:
        data = json.loads((path or PALETTE_FILE).read_text())
        pal = [str(c["led"]).lstrip("#").lower() for c in data["cells"]]
        if not all(len(h) == 6 for h in pal):
            pal = []
    except (OSError, ValueError, KeyError, TypeError):
        pal = []
    if path is None:
        _LED_CACHE = pal
    return pal


def fnv1a32(text):
    """FNV-1a 32-bit over UTF-8 bytes of the NFC-normalized string.

    The frozen colorhash contract: FNV1a32(UTF8(NFC(name))). Golden vector —
    "café" -> 0xa82b5049. Must agree byte-for-byte with wsid_hash in
    workspace-identity-lib and with lab.html's fnv1a, or pad and bar drift apart.
    """
    h = 0x811C9DC5
    for b in unicodedata.normalize("NFC", text).encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def session_name(label):
    """Canonical tmux/Hyprland task-space name.

    Golden twin of wsid_session_name in ~/oracle/scripts/workspace-identity-lib:
    ASCII controls/whitespace and . : / # become '-', repeated dashes collapse,
    and edge dashes disappear. Keep both implementations byte-for-byte aligned.
    """
    s = "".join(
        "-" if ord(ch) <= 32 or ord(ch) == 127 or ch in ".:/#" else ch
        for ch in label
    )
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def ws_color(name):
    """colorhash(sanitized session name) -> LED hex, or None when there is no color.

    Hashes the SANITIZED form so the bar (which hashes plain workspace names)
    and the pad/tmux side (which hashes the sanitized session name) agree even
    when a name needs sanitizing, e.g. workspace "wacky sax" -> session
    "wacky-sax".

    There is deliberately no `light` parameter. One used to select between two
    theme-tuned palettes; colorhash's `led` surface is a physical rendering on
    WS2812s, which have no theme ground to contrast against, so it was accepted
    and ignored -- and an ignored parameter kept a whole theme-lightness chain
    (HyprState.light, _on_palette's light.mode probe) looking load-bearing when
    nothing read it. Should a theme-dependent surface ever land, give it a
    parameter then.
    """
    if not name or name.isdigit():
        return None
    pal = led_palette()
    if not pal:
        return None
    return pal[fnv1a32(session_name(name)) % len(pal)]


def name_color(name, pal):
    """colorhash(tmux WINDOW name) -> a cell of `pal`, or None when there is none.

    Sibling of ws_color, deliberately NOT the same function. ws_color sanitizes
    first because a workspace's color must agree with the SESSION name derived
    from its label; a tmux window name is already the exact string the bar
    hashes -- wsid-tmux-colors' stamp_window feeds `#{window_name}` straight to
    wsid_cell -- so sanitizing here would split the two apart. It also does not
    treat an all-digit name as unnamed: a workspace named "3" is Hyprland's way
    of saying "no label", but a tmux window named "3" is a window called 3, and
    the bar colors it.

    Takes `pal` as an argument rather than calling led_palette() so ctx_windows
    can stay a pure function of its inputs (its tests pass a fake palette).
    Empty palette or empty name -> None: fail closed on color, never invent one.
    """
    if not pal or not name:
        return None
    return pal[fnv1a32(name) % len(pal)]


def ws_label(name):
    """Human label from a workspace name, or None for unnamed (bare-digit) names."""
    if not name:
        return None
    text = name.strip()
    if not text or text.isdigit():
        return None
    return text


REFRESH_EVENTS = {
    "workspace", "workspacev2", "focusedmon", "openwindow", "closewindow",
    "movewindow", "activewindow", "activewindowv2", "urgent", "fullscreen",
    "monitoradded", "monitorremoved", "renameworkspace",
}


def find_instance_dir(runtime):
    best = best_m = None
    for d in Path(runtime).glob("hypr/*"):
        try:
            if (d / ".socket.sock").exists():
                m = d.stat().st_mtime
                if best is None or m > best_m:
                    best, best_m = d, m
        except OSError:
            continue
    return best


async def request(instance_dir, cmd):
    reader, writer = await asyncio.open_unix_connection(str(instance_dir / ".socket.sock"))
    writer.write(cmd.encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data


def parse_event(line):
    if ">>" not in line:
        return None
    name, _, data = line.partition(">>")
    return name, data


class HyprState:
    def __init__(self):
        # Workspace state feeding the {"t": "ws"} message -- the split deck's
        # top half renders it on keys 0-5, so this wire has a consumer again.
        self.active = 1
        self.occupied = []
        self.urgent_ws = []
        self.colors = {}
        self.names = {}
        self.cls = ""
        self.title = ""
        self.addr = ""
        self.submap = ""
        self.screencast = False
        self._urgent_addrs = set()
        # Raw IPC snapshots, retained for the ctx poll (hyprland.ctx_windows /
        # deck_bells need both). Initialised empty so a poll firing before
        # the first refresh cannot raise.
        self.clients = []
        self.workspaces = []

    def handle_event(self, name, data):
        """Returns (needs_refresh, flags_changed)."""
        if name == "submap":
            changed = data != self.submap
            self.submap = data
            return False, changed
        if name == "screencast":
            on = data.split(",")[0] == "1"
            changed = on != self.screencast
            self.screencast = on
            return False, changed
        # "bell" is the xdg-system-bell spelling of urgency: Hyprland >= 0.5x
        # emits it (and does NOT set client urgency or an "urgent" event) when a
        # terminal rings BEL via the system-bell protocol, which foot now uses.
        if name in ("urgent", "bell"):
            self._urgent_addrs.add(data.removeprefix("0x"))
            return True, False
        return name in REFRESH_EVENTS, False

    def refresh(self, workspaces, active_ws, active_win, clients):
        """Re-read Hyprland's world. Returns the wire messages that changed:
        {"t": "ws"} for the top half of the split deck, {"t": "win"} for the
        OLED's focused-window line."""
        msgs = []
        self.clients = clients
        self.workspaces = workspaces
        active = active_ws.get("id", 1)
        occupied = sorted(w["id"] for w in workspaces if w.get("windows", 0) > 0)
        addr_ws = {
            str(c.get("address", "")).removeprefix("0x"): c.get("workspace", {}).get("id")
            for c in clients
        }
        self._urgent_addrs = {
            a for a in self._urgent_addrs
            if addr_ws.get(a) is not None and addr_ws[a] != active
        }
        # Note what that prune consumes: `active`. A bell is forgotten once you
        # are looking at the workspace that rang, so the set is not merely
        # accumulated -- both the ws message's urgent list and deck_bells read it.
        urgent = sorted({addr_ws[a] for a in self._urgent_addrs})
        colors = {}
        names = {}
        for w in workspaces:
            c = ws_color(w.get("name"))
            if c is not None:
                colors[str(w["id"])] = c
            lbl = ws_label(w.get("name"))
            if lbl is not None:
                names[str(w["id"])] = lbl
        if (active, occupied, urgent, colors, names) != (
                self.active, self.occupied, self.urgent_ws, self.colors, self.names):
            self.active, self.occupied, self.urgent_ws, self.colors, self.names = (
                active, occupied, urgent, colors, names)
            msgs.append(self._ws_msg())
        cls = (active_win or {}).get("class", "")
        title = (active_win or {}).get("title", "")[:60]
        self.addr = str((active_win or {}).get("address", ""))
        if (cls, title) != (self.cls, self.title):
            self.cls, self.title = cls, title
            msgs.append(self._win_msg())
        return msgs

    def snapshot(self):
        """Both wire messages, for replaying to a pad that just (re)connected."""
        return [self._ws_msg(), self._win_msg()]

    def _ws_msg(self):
        return {"t": "ws", "active": self.active, "occupied": self.occupied,
                "urgent": self.urgent_ws, "colors": self.colors,
                "names": self.names}

    def _win_msg(self):
        return {"t": "win", "cls": self.cls, "title": self.title}


# A key is for something that can ASK FOR YOU and that you can GO TO locally.
# Terminals qualify on both counts: a tmux window rings via window_bell_flag, a
# bare terminal rings via Hyprland's bell event (foot.ini [bell] urgent=yes).
# A browser does neither, and three of them would eat a quarter of the deck.
TERMINAL_CLASSES = (
    "foot", "footclient", "alacritty", "kitty", "ghostty",
    "com.mitchellh.ghostty",
)

WS_ID_LAST = 1 << 30      # sorts after any real Hyprland workspace id


def _ws_sort_id(wsobj):
    """Workspace id for filtering/ordering, or a sentinel that matches nothing.

    Hyprland always supplies an integer id for a normal client, but a missing or
    non-integer one would raise TypeError against real ints -- which _poll_ctx
    catches, so the symptom would be a bottom deck that silently stops updating
    rather than anything visible. Degrade to the sentinel instead. bool is
    excluded because it is an int subclass (True would match workspace 1).
    """
    v = wsobj.get("id") if isinstance(wsobj, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) else WS_ID_LAST


def _ws_obj(c):
    """A client's workspace as a dict, whatever Hyprland actually sent.

    Sibling of _ws_sort_id and for the same reason: a client with
    "workspace": null would raise AttributeError inside _poll_ctx -- which
    catches Exception broadly, so the symptom is a bottom deck that silently
    stops updating, not a visible failure.
    """
    ws = c.get("workspace") if isinstance(c, dict) else None
    return ws if isinstance(ws, dict) else {}


def _is_terminal(cls):
    c = str(cls or "").lower()
    return c in TERMINAL_CLASSES or c.startswith("ws-")


CTX_KEYS = 6      # the bottom half of the pad


def ctx_windows(tmux_windows, clients, associations, active, pal, focused_addr="",
                bells=frozenset()):
    """The bottom deck: windows on workspace id `active` that earn a key.

    tmux windows from explicitly associated local clients come first, by
    (session, index); sessionless terminals follow, by address. At most
    CTX_KEYS items.

    Color is identity, and identity is the NAME: a tmux window wears
    colorhash(window name), so it keeps its hue across a move, a swap and a
    renumber, and matches the pill the status bar draws for it. Until
    2026-08-23 this was pal[(index - 1) % len(pal)] instead -- true when cockpit
    v2 shipped, and stale from the moment wsid-tmux-colors retired its
    @wsid_win1..6 per-index bins for a name hash (see that script's header).
    The tell was two same-named windows: the bar drew one color, the pad drew
    two, which a per-index scheme can never avoid.

    A sessionless terminal is NOT name-hashed. Its only name is a Hyprland
    title that changes on every cd, so hashing it would make the key flicker,
    and there is no bar entry for it to agree with anyway -- it takes the cell
    of the key it lands on. An empty palette sends None for both -- fail closed
    on color, never invent one.

    Each item carries its jump target: `s`/`i` for tmux plus the session
    client's `addr` (focus the terminal first, then select-window), or just
    `addr` for a bare terminal.
    """
    associated_addrs = {a["address"] for a in associations}
    chosen = {}
    for assoc in associations:
        if _ws_sort_id(assoc.get("workspace")) != active:
            continue
        key = assoc["session"]
        rank = (assoc["focusHistoryID"], assoc["address"])
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, assoc)
    sessions = {session: pair[1]["address"] for session, pair in chosen.items()}

    entries = []

    def add_tmux(session_map):
        for w in sorted(tmux_windows, key=lambda w: (w["s"], w["i"])):
            addr = session_map.get(w["s"])
            if addr is None:
                continue
            entries.append({"id": w["id"], "n": "%d %s" % (w["i"], w["n"]),
                            "s": w["s"], "i": w["i"],
                            "addr": addr, "active": bool(w["active"]),
                            "c": name_color(w["n"], pal)})

    add_tmux(sessions)
    for c in sorted(clients, key=lambda c: str(c.get("address", ""))):
        addr = str(c.get("address", ""))
        if (addr in associated_addrs or not _is_terminal(c.get("class"))
                or _ws_sort_id(_ws_obj(c)) != active):
            continue
        entries.append({"id": "hypr:" + addr,
                        "n": str(c.get("title", "") or c.get("class", "")),
                        "addr": addr, "active": addr == focused_addr,
                        "c": pal[len(entries) % len(pal)] if pal else None})
    entries = entries[:CTX_KEYS]
    for e in entries:
        e["bell"] = e["id"] in bells
    return entries


def deck_bells(tmux_windows, urgent_addrs, associations):
    """Window ids with an unacked bell. Spec section 6.1.

    A single BEL inside an associated terminal fires BOTH channels: tmux sets
    window_bell_flag, and foot rings the system bell for the surrounding client,
    which Hyprland emits as `bell`. The tmux flag names the exact window; the
    Hyprland event names only the terminal. So where a session exists the tmux
    flag WINS and the Hyprland event is discarded -- otherwise one bell would
    light the right key and smear across every key of that session.
    """
    out = {w["id"] for w in tmux_windows if w.get("bell")}
    associated = {str(a["address"]).removeprefix("0x") for a in associations}
    for addr in urgent_addrs:
        normalized = str(addr).removeprefix("0x")
        if normalized not in associated:
            out.add("hypr:0x" + normalized)
    return out
