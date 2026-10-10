import asyncio
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from macropadd import tmux


LOCAL = [{
    "session": "oracle",
    "address": "0xabc",
    "pid": 123,
    "workspace": {"id": 3, "name": "oracle"},
    "class": "kitty",
    "focusHistoryID": 0,
}]


def test_select_uses_exact_session_match():
    calls = []

    async def fake(*args):
        calls.append(args)
        return 0, ""

    asyncio.run(tmux.select_window("mac", 3, run=fake))
    assert calls[0] == ("select-window", "-t", "=mac:3")


def test_select_window_false_on_failure():
    async def bad_rc(*args):
        return 1, ""

    async def raises(*args):
        raise OSError("gone")

    assert asyncio.run(tmux.select_window("s", 1, run=bad_rc)) is False
    assert asyncio.run(tmux.select_window("s", 1, run=raises)) is False


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_against_real_tmux_isolated_server(tmp_path):
    """Real tmux, isolated socket + own conf: the daily server is untouched."""
    conf = tmp_path / "tmux.conf"
    conf.write_text("set -g base-index 1\nset -g renumber-windows on\n")
    sock = f"km-test-{uuid.uuid4().hex[:8]}"
    tm = ["tmux", "-L", sock, "-f", str(conf)]
    subprocess.run(tm + ["new-session", "-d", "-s", "alpha", "-x", "80", "-y", "24"],
                   check=True)
    try:
        subprocess.run(tm + ["new-window", "-t", "=alpha", "-n", "second"], check=True)

        async def run(*args):
            proc = await asyncio.create_subprocess_exec(
                *tm, *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            return proc.returncode, out.decode()

        subprocess.run(tm + ["new-window", "-t", "=alpha", "-n", "agent", "sleep 60"],
                       check=True)
        subprocess.run(tm + ["set-option", "-p", "-t", "=alpha:3",
                             "@claude_state", "blocked"], check=True)
        subprocess.run(tm + ["set-option", "-p", "-t", "=alpha:2",
                             "@claude_state", "blocked"], check=True)
        subprocess.run(tm + ["select-window", "-t", "=alpha:2"], check=True)
        # A just-created pane reports `tmux` as its command until the fork execs
        # the shell (or `sleep`), so wait for both to settle before reading.
        want = {"2": "bash", "3": "sleep"}
        for _ in range(100):
            got = subprocess.run(
                tm + ["list-windows", "-t", "=alpha", "-F",
                      "#{window_index} #{pane_current_command}"],
                check=True, capture_output=True, text=True).stdout.split("\n")
            seen = dict(line.split(" ", 1) for line in got if line)
            if all(seen.get(k) == v for k, v in want.items()):
                break
            time.sleep(0.02)

        async def scenario():
            wins = await tmux.list_deck_windows(run=run)
            wins = [w for w in wins if w["s"] == "alpha"]
            ok = await tmux.select_window("alpha", 1, run=run)
            wins2 = await tmux.list_deck_windows(run=run)
            wins2 = [w for w in wins2 if w["s"] == "alpha"]
            return wins, ok, wins2

        wins, ok, wins2 = asyncio.run(scenario())
        assert [w["i"] for w in wins] == [1, 2, 3]
        assert wins[1]["active"] is True          # select-window made it active
        # Window 3 runs `sleep` (not a shell) and is blocked; window 2 is a
        # shell carrying a stale `blocked`, which the guard drops.
        assert [w["blocked"] for w in wins] == [False, False, True]
        assert ok is True
        assert wins2[0]["active"] is True         # select-window took effect
    finally:
        subprocess.run(tm + ["kill-server"], check=False)


def test_parse_deck_windows_uses_tmux_window_id_not_session_index():
    # Field order (see tmux.DECK_FORMAT for the full note): session,
    # window_id, index, active, bell, panes, name -- active before bell, the
    # free-text name LAST. Rows 1-2 deliberately have active/bell asymmetric so
    # a swap between the two would fail this test; the third row is all-zero
    # and can't catch it.
    out = ("mirepoix\t@2\t1\t1\t0\tbash=;\trails\n"
           "mirepoix\t@5\t3\t0\t1\tbash=;\tlogs\n"
           "colorhash\t@9\t1\t0\t0\tbash=;\tlab\n")
    got = tmux.parse_deck_windows(out)
    assert got == [
        {"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails",
         "active": True, "bell": False, "blocked": False},
        {"id": "tmux:@5", "s": "mirepoix", "i": 3, "n": "logs",
         "active": False, "bell": True, "blocked": False},
        {"id": "tmux:@9", "s": "colorhash", "i": 1, "n": "lab",
         "active": False, "bell": False, "blocked": False},
    ]


def test_parse_deck_windows_separates_blocked_from_bell():
    # Row 1 is blocked WITHOUT a bell (you looked, didn't answer); row 2 rang
    # but is not blocked. A swap of the bell and panes fields fails here.
    out = ("mirepoix\t@2\t1\t0\t0\tclaude=blocked;\tagent\n"
           "mirepoix\t@5\t2\t0\t1\tclaude=idle;\tdone\n")
    got = tmux.parse_deck_windows(out)
    assert [(w["n"], w["bell"], w["blocked"]) for w in got] == [
        ("agent", False, True), ("done", True, False)]


def test_parse_deck_windows_blocked_needs_one_live_claude_pane():
    out = (
        # A split window: one shell pane, one blocked Claude -> blocked.
        "s\t@1\t1\t0\t0\tbash=;claude=blocked;\tsplit\n"
        # STALE: the Claude died without SessionEnd and the pane is back at a
        # shell prompt, still carrying `blocked` -> not blocked.
        "s\t@2\t2\t0\t0\tbash=blocked;\tstale\n"
        "s\t@3\t3\t0\t0\tzsh=blocked;fish=blocked;sh=blocked;dash=blocked;\tshells\n"
        # Other states are not blocked.
        "s\t@4\t4\t0\t0\tclaude=busy;claude=waiting;claude=idle;\tworking\n"
        # A name with a tab survives: the name is the last field.
        "s\t@5\t5\t0\t0\tclaude=blocked;\ta\tb\n")
    got = {w["n"]: w["blocked"] for w in tmux.parse_deck_windows(out)}
    assert got == {"split": True, "stale": False, "shells": False,
                   "working": False, "a\tb": True}


def test_parse_deck_windows_skips_malformed_and_unparseable_index():
    out = ("sess\t@1\tnotanint\t0\t0\tbash=;\tname\n"
           "sess\t@2\ttoo\tfew\n\nok\t@3\t2\t0\t0\tbash=;\tn\n")
    assert [x["id"] for x in tmux.parse_deck_windows(out)] == ["tmux:@3"]


def test_deck_format_requests_window_id():
    assert "#{window_id}" in tmux.DECK_FORMAT
    assert "#{window_bell_flag}" in tmux.DECK_FORMAT
    assert "#{@claude_state}" in tmux.DECK_FORMAT
    assert "#{pane_current_command}" in tmux.DECK_FORMAT
    assert tmux.DECK_FORMAT.endswith("#{window_name}")   # free text stays last


def test_local_clients_command_uses_installed_helper():
    assert tmux.LOCAL_CLIENTS_COMMAND == Path.home() / ".local/bin/tmux-local-clients"


def test_parse_local_clients_accepts_contract():
    assert tmux.parse_local_clients(json.dumps(LOCAL)) == LOCAL


def test_parse_local_clients_accepts_empty_array():
    assert tmux.parse_local_clients("[]") == []


@pytest.mark.parametrize("out", [
    "not json",
    json.dumps({}),
    json.dumps(["not an object"]),
    json.dumps([{}]),
    json.dumps([{**LOCAL[0], "session": ""}]),
    json.dumps([{**LOCAL[0], "address": ""}]),
    json.dumps([{**LOCAL[0], "pid": True}]),
    json.dumps([{**LOCAL[0], "pid": "123"}]),
    json.dumps([{**LOCAL[0], "pid": 0}]),
    json.dumps([{**LOCAL[0], "pid": -1}]),
    json.dumps([{**LOCAL[0], "workspace": []}]),
    json.dumps([{**LOCAL[0], "workspace": {"id": True, "name": "oracle"}}]),
    json.dumps([{**LOCAL[0], "workspace": {"id": "3", "name": "oracle"}}]),
    json.dumps([{**LOCAL[0], "workspace": {"id": 3}}]),
    json.dumps([{**LOCAL[0], "workspace": {"id": 3, "name": 4}}]),
    json.dumps([{**LOCAL[0], "class": None}]),
    json.dumps([{**LOCAL[0], "focusHistoryID": False}]),
    json.dumps([{**LOCAL[0], "focusHistoryID": "0"}]),
    json.dumps([{**LOCAL[0], "focusHistoryID": -1}]),
])
def test_parse_local_clients_rejects_invalid_contract(out):
    with pytest.raises(ValueError):
        tmux.parse_local_clients(out)


@pytest.mark.parametrize("snapshot", [LOCAL, []])
def test_list_local_clients_returns_valid_snapshot(snapshot):
    async def fake():
        return 0, json.dumps(snapshot)

    assert asyncio.run(tmux.list_local_clients(run=fake)) == snapshot


def test_list_local_clients_returns_none_on_nonzero_exit():
    async def fake():
        return 1, json.dumps(LOCAL)

    assert asyncio.run(tmux.list_local_clients(run=fake)) is None


def test_list_local_clients_returns_none_on_oserror():
    async def fake():
        raise OSError("helper unavailable")

    assert asyncio.run(tmux.list_local_clients(run=fake)) is None


def test_list_local_clients_returns_none_on_invalid_output():
    async def fake():
        return 0, "not json"

    assert asyncio.run(tmux.list_local_clients(run=fake)) is None
