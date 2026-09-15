from pathlib import Path

import pytest

from macropadd import hyprland
from macropadd.hyprland import HyprState, find_instance_dir, parse_event

WORKSPACES = [
    {"id": 1, "windows": 2}, {"id": 2, "windows": 0}, {"id": 5, "windows": 1},
]
ACTIVE_WS = {"id": 1}
WIN = {"class": "foot", "title": "vim ~/notes.md"}
CLIENTS = [
    {"address": "0x5f2280", "workspace": {"id": 5}},
    {"address": "0x5f9000", "workspace": {"id": 1}},
]


def test_parse_event():
    assert parse_event("workspace>>3") == ("workspace", "3")
    assert parse_event("activewindow>>foot,a,b") == ("activewindow", "foot,a,b")
    assert parse_event("garbage") is None


def test_find_instance_dir_picks_dir_with_socket(tmp_path):
    a = tmp_path / "hypr" / "aaa"; a.mkdir(parents=True)
    b = tmp_path / "hypr" / "bbb"; b.mkdir(parents=True)
    (b / ".socket.sock").touch()
    assert find_instance_dir(tmp_path) == b


def test_refresh_returns_ws_and_win_messages_on_first_snapshot():
    # The split deck's top half consumes {"t": "ws"} again -- the write-only-wire
    # era (no firmware reader) ended when cockpit went back to rendering
    # workspaces on keys 0-5.
    s = HyprState()
    msgs = {m["t"]: m for m in s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)}
    assert msgs["ws"]["active"] == 1
    assert msgs["ws"]["occupied"] == [1, 5]      # windows > 0 only
    assert msgs["ws"]["urgent"] == []
    assert msgs["win"]["title"] == "vim ~/notes.md"
    assert s.clients == CLIENTS                  # still retained for the ctx poll


def test_refresh_returns_only_what_changed():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS) == []
    msgs = s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)
    assert [m["t"] for m in msgs] == ["ws"]


def test_ws_message_carries_colorhash_colors_and_names_by_id():
    s = HyprState()
    workspaces = [
        {"id": 1, "name": "mirepoix", "windows": 1},
        {"id": 2, "name": "2", "windows": 1},          # unnamed: no color, no name
    ]
    (msg,) = [m for m in s.refresh(workspaces, {"id": 1}, None, [])
              if m["t"] == "ws"]
    assert msg["colors"] == {"1": "be5000"}
    assert msg["names"] == {"1": "mirepoix"}


def test_urgent_workspaces_reach_the_ws_message_and_clear_on_focus():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    s.handle_event("urgent", "5f2280")               # client on ws 5
    (msg,) = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert msg["urgent"] == [5]
    msgs = s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)   # looking at it clears it
    assert all(m["urgent"] == [] for m in msgs if m["t"] == "ws")


def test_snapshot_replays_ws_and_win_for_a_reconnecting_pad():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert [m["t"] for m in s.snapshot()] == ["ws", "win"]


def test_urgent_event_address_is_normalized_and_cleared_on_focus():
    # The surviving consumer of urgency is deck_bells, which reads _urgent_addrs
    # directly -- addresses, not the workspace-id list the ws message carried.
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    needs_refresh, _ = s.handle_event("urgent", "5f2280")   # event has NO 0x prefix
    assert needs_refresh
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert s._urgent_addrs == {"5f2280"}
    s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)          # focusing ws 5 clears it
    assert s._urgent_addrs == set()


def test_bell_event_is_treated_as_urgent():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    needs_refresh, _ = s.handle_event("bell", "5f2280")
    assert needs_refresh
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert s._urgent_addrs == {"5f2280"}
    s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)
    assert s._urgent_addrs == set()


def test_submap_and_screencast_touch_flags_only():
    s = HyprState()
    assert s.handle_event("submap", "resize") == (False, True)
    assert s.submap == "resize"
    assert s.handle_event("submap", "resize") == (False, False)
    assert s.handle_event("screencast", "1,0") == (False, True)
    assert s.screencast is True


def test_fnv1a32_matches_the_colorhash_contract():
    # The frozen vectors from ~/.local/share/chezmoi/colorhash/vectors.json, which
    # lab.html's runSelfTest and the bash wsid_hash check against too. If this drifts,
    # the pad and the status bar color the same session differently.
    from macropadd.hyprland import fnv1a32
    assert fnv1a32("") == 0x811C9DC5
    assert fnv1a32("a") == 0xE40C292C
    assert fnv1a32("mirepoix") == 0x2F81FEBA
    assert fnv1a32("oracle") == 0xF4E20EF7
    assert fnv1a32("café") == 0xA82B5049      # NFC-normalized before encoding
    assert fnv1a32("recipe box") == 0x9A56501A


def test_fnv1a32_normalizes_to_nfc():
    # The same café spelled with a combining acute must hash identically. This is the
    # only part of the contract Python enforces that bash cannot.
    from macropadd.hyprland import fnv1a32
    assert fnv1a32("café") == fnv1a32("café")


def test_ws_color_hashes_plain_name():
    from macropadd.hyprland import ws_color
    assert ws_color("mirepoix") == "be5000"          # cell 6, led surface
    assert ws_color("oracle") == "dd9100"            # cell 1, led surface
    assert ws_color("4") is None                     # unnamed: bare id, no color
    assert ws_color("") is None
    assert ws_color(None) is None


def test_ws_color_takes_the_led_surface_not_the_fill():
    # palette.json's five columns are not interchangeable. `bg` is Petroff's published
    # fill (#e76300 for cell 6); `led` is the WS2812 rendering (#be5000 since the
    # 2026-08-23 led-band re-saturation). Sending the fill would push a color that
    # was never checked against the pad's hardware.
    from macropadd.hyprland import ws_color
    assert ws_color("mirepoix") != "e76300"
    assert ws_color("mirepoix") == "be5000"


def test_ws_color_is_none_without_a_palette(tmp_path, monkeypatch):
    # Fail closed: an unlit pad is obvious, a plausible-but-wrong palette is not.
    from macropadd import hyprland
    monkeypatch.setattr(hyprland, "PALETTE_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(hyprland, "_LED_CACHE", None)
    assert hyprland.ws_color("mirepoix") is None


def test_session_name_matches_the_bash_canonicalizer():
    # Golden twin of wsid_session_name in ~/oracle/scripts/workspace-identity-lib.
    from macropadd.hyprland import session_name

    assert session_name("a.b:c/d#e") == "a-b-c-d-e"
    assert session_name("a\tb\nc\x7fd") == "a-b-c-d"
    assert session_name("a.#-b") == "a-b"
    assert session_name(" .#x: ") == "x"
    assert session_name("café-日本語") == "café-日本語"
    assert session_name(" #.:/\t") == ""


def test_ws_color_hashes_sanitized_name():
    from macropadd.hyprland import ws_color
    # "wacky sax" sanitizes to "wacky-sax"; fnv1a32("wacky-sax")=0xaecd57d7, mod 10 = 1.
    assert ws_color("wacky sax") == "dd9100"
    assert ws_color("wacky sax") == ws_color("wacky-sax")


def test_refresh_tracks_active_window_address():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, {"class": "foot", "title": "t", "address": "0xf00"}, [])
    assert s.addr == "0xf00"
    s.refresh(WORKSPACES, ACTIVE_WS, None, [])    # no active window
    assert s.addr == ""


def test_renameworkspace_triggers_refresh():
    # Hyprland emits renameworkspace>>ID,NAME (verified live 2026-08-18).
    # Without it in REFRESH_EVENTS a rename only reaches the pad by luck,
    # riding whatever unrelated event fires next.
    from macropadd.hyprland import HyprState
    s = HyprState()
    assert s.handle_event("renameworkspace", "1,wacky-sax") == (True, False)


DECK_CLIENTS = [
    {"address": "0xaa", "class": "kitty", "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xbb", "class": "firefox",     "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xcc", "class": "foot",        "workspace": {"id": 1, "name": "bonsai"}},
    {"address": "0xdd", "class": "foot",        "workspace": {"id": 4, "name": "4"}},
]
DECK_ASSOCIATIONS = [{"session": "mirepoix", "address": "0xaa"}]


def test_tmux_flag_wins_where_a_session_exists():
    twins = [{"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails",
              "active": False, "bell": True}]
    # One BEL fires BOTH channels: tmux flags the window and foot rings the
    # system bell for the associated client. Counting both would smear the alert
    # across every key of that session.
    got = hyprland.deck_bells(twins, {"aa"}, DECK_ASSOCIATIONS)
    assert got == {"tmux:@2"}
    assert "hypr:0xaa" not in got


def test_hyprland_bell_is_authoritative_only_for_sessionless_clients():
    assert hyprland.deck_bells([], {"cc"}, DECK_ASSOCIATIONS) == {"hypr:0xcc"}


def test_deck_bells_suppresses_only_exact_association_addresses():
    got = hyprland.deck_bells([], {"aa", "bb"}, DECK_ASSOCIATIONS)
    assert got == {"hypr:0xbb"}


def test_stale_ws_class_without_association_keeps_coarse_bell():
    assert hyprland.deck_bells([], {"dd"}, []) == {"hypr:0xdd"}


def test_no_bells_is_an_empty_set_not_none():
    assert hyprland.deck_bells([], set(), DECK_ASSOCIATIONS) == set()


PAL10 = ["c00", "c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09"]
CTX_CLIENTS = [
    {"address": "0xaa", "class": "kitty", "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xbb", "class": "firefox", "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xcc", "class": "foot", "workspace": {"id": 2, "name": "mirepoix"}, "title": "bare"},
    {"address": "0xdd", "class": "com.mitchellh.ghostty", "workspace": {"id": 1, "name": "bonsai"}},
]
CTX_ASSOCIATIONS = [{
    "session": "mirepoix", "address": "0xaa", "pid": 100,
    "workspace": {"id": 2, "name": "mirepoix"},
    "class": "kitty", "focusHistoryID": 0,
}]
CTX_TWINS = [
    {"id": "tmux:@3", "s": "mirepoix", "i": 2, "n": "logs", "active": False, "bell": False},
    {"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails", "active": True, "bell": False},
    {"id": "tmux:@9", "s": "bonsai", "i": 1, "n": "notes", "active": True, "bell": False},
]


def test_ctx_windows_is_the_active_workspace_only_tmux_first_by_index():
    got = hyprland.ctx_windows(CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert [w["id"] for w in got] == ["tmux:@2", "tmux:@3", "hypr:0xcc"]
    # bonsai's tmux window and terminal live on ws 1: not this deck's business


def test_ctx_windows_colors_tmux_by_name_hash_terminals_by_key_position():
    # A tmux window wears colorhash(WINDOW NAME) -- the same cell the status
    # bar paints, because wsid-tmux-colors stamps @wsid_wfg/@wsid_wbg from the
    # window's name. Golden cells: "rails" -> 0, "logs" -> 2. A sessionless
    # terminal has no tmux name for the bar to agree with (its title churns on
    # every cd), so it keeps the cell of the key it lands on.
    got = hyprland.ctx_windows(CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert [w["c"] for w in got] == ["c00", "c02", "c02"]
    got2 = hyprland.ctx_windows(
        [{"id": "tmux:@4", "s": "mirepoix", "i": 5, "n": "x",
          "active": False, "bell": False}], CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert got2[0]["c"] == "c05"                # hash("x") -> cell 5; index 5 unconsulted


def test_ctx_windows_gives_two_windows_of_one_name_one_color():
    # The signature of the index-keyed scheme this replaced: under
    # pal[(i - 1) % len(pal)] two ADJACENT keys can never share a cell, so a
    # same-named pair lit as two hues on the pad while the bar drew one. That
    # asymmetry is how the drift was spotted. Collisions are expected at n=10.
    twins = [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "logs",
              "active": False, "bell": False},
             {"id": "tmux:@2", "s": "mirepoix", "i": 2, "n": "logs",
              "active": False, "bell": False}]
    got = hyprland.ctx_windows(twins, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert [w["c"] for w in got[:2]] == ["c02", "c02"]


def test_ctx_windows_without_a_palette_sends_no_color():
    got = hyprland.ctx_windows(CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, [])
    assert all(w["c"] is None for w in got)     # fail closed on color


def test_ctx_windows_active_and_bell_flags():
    bells = {"tmux:@3", "hypr:0xcc"}
    got = {w["id"]: w for w in hyprland.ctx_windows(
        CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10,
        focused_addr="0xcc", bells=bells)}
    assert got["tmux:@2"]["active"] is True     # tmux's own active flag
    assert got["tmux:@3"]["bell"] is True
    assert got["hypr:0xcc"]["active"] is True   # holds Hyprland focus
    assert got["hypr:0xcc"]["bell"] is True


def test_ctx_windows_carries_jump_targets():
    got = {w["id"]: w for w in hyprland.ctx_windows(
        CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)}
    assert got["tmux:@2"]["s"] == "mirepoix" and got["tmux:@2"]["i"] == 1
    assert got["tmux:@2"]["addr"] == "0xaa"     # selected association address
    assert got["hypr:0xcc"]["addr"] == "0xcc"


@pytest.mark.parametrize("terminal_class", ["kitty", "foot", "com.mitchellh.ghostty"])
def test_ctx_windows_expands_associated_terminal_classes(terminal_class):
    clients = [{"address": "0xaa", "class": terminal_class,
                "workspace": {"id": 2}}]
    associations = [{"session": "mirepoix", "address": "0xaa",
                     "workspace": {"id": 2}, "focusHistoryID": 0}]
    got = hyprland.ctx_windows(CTX_TWINS, clients, associations, 2, PAL10)
    assert [w["id"] for w in got] == ["tmux:@2", "tmux:@3"]


def test_ctx_windows_filters_associations_to_active_workspace():
    got = hyprland.ctx_windows(CTX_TWINS, CTX_CLIENTS, CTX_ASSOCIATIONS, 1, PAL10)
    assert [w["id"] for w in got] == ["hypr:0xdd"]


def test_ctx_windows_chooses_lowest_history_then_address_for_duplicate_session():
    clients = [
        {"address": "0xac", "class": "kitty", "workspace": {"id": 2}},
        {"address": "0xab", "class": "foot", "workspace": {"id": 2}},
        {"address": "0xaa", "class": "foot", "workspace": {"id": 2}},
    ]
    associations = [
        {"session": "mirepoix", "address": "0xac", "workspace": {"id": 2},
         "focusHistoryID": 2},
        {"session": "mirepoix", "address": "0xab", "workspace": {"id": 2},
         "focusHistoryID": 1},
        {"session": "mirepoix", "address": "0xaa", "workspace": {"id": 2},
         "focusHistoryID": 1},
    ]
    got = hyprland.ctx_windows(CTX_TWINS, clients, associations, 2, PAL10)
    assert got[0]["addr"] == "0xaa"
    assert all(not item["id"].startswith("hypr:") for item in got)


def test_ctx_windows_leaves_unmatched_terminal_sessionless():
    got = hyprland.ctx_windows([], CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert [w["id"] for w in got] == ["hypr:0xcc"]


def test_ctx_windows_survives_null_and_boolean_workspaces():
    # Hyprland can send "workspace": null, and bool is an int subclass. Either
    # would raise inside _poll_ctx -- which catches Exception broadly, so the
    # symptom is a bottom deck that silently stops updating, not a crash.
    clients = [
        {"address": "0xaa", "class": "foot", "workspace": None, "title": "null-ws"},
        {"address": "0xbb", "class": "foot", "workspace": {"id": True, "name": "b"},
         "title": "boolish"},
        {"address": "0xcc", "class": "foot", "workspace": {"id": 2, "name": "y"},
         "title": "real"},
    ]
    got = hyprland.ctx_windows([], clients, [], 2, PAL10)
    assert [w["n"] for w in got] == ["real"]


def test_ctx_windows_caps_at_six_keys():
    twins = [{"id": "tmux:@%d" % i, "s": "mirepoix", "i": i, "n": "w%d" % i,
              "active": False, "bell": False} for i in range(1, 9)]
    got = hyprland.ctx_windows(twins, CTX_CLIENTS, CTX_ASSOCIATIONS, 2, PAL10)
    assert len(got) == 6
    assert got[-1]["id"] == "tmux:@6"           # overflow drops from the end
