import asyncio
import json
import os
from pathlib import Path

import pytest

import km_proto
from operatord.__main__ import Config, Supervisor


@pytest.fixture(autouse=True)
def no_live_audio(monkeypatch):
    async def idle(*args):
        await asyncio.Future()
    monkeypatch.setattr("operatord.spectrum.watch", idle)
    monkeypatch.setattr("operatord.media.watch", idle)

WORKSPACES = [{"id": 1, "windows": 1}, {"id": 3, "windows": 2}]


def _supervisor(tmp_path):
    """A Supervisor with no live link, for testing key/ctx logic synchronously.

    SerialLink.__init__ only stores its path (serial_link.py:11-18) and send() no-ops
    while disconnected, so the device never has to exist. The async tests below build
    their own Supervisor against a real pty because they exercise the link itself;
    this one deliberately does not.
    """
    cfg = Config(device=str(tmp_path / "no-such-device"), runtime_dir=tmp_path,
                 home=tmp_path, state_dir=tmp_path)
    return Supervisor(cfg)


def _sent_link(sink):
    return type("L", (), {"send": staticmethod(lambda m: sink.append(m) or True)})()


class FakeHypr:
    """Serves .socket.sock (requests) and .socket2.sock (events)."""

    def __init__(self):
        self.dispatched = []
        self.event_writer = None

    async def start(self, instance_dir):
        instance_dir.mkdir(parents=True)
        await asyncio.start_unix_server(self._req, path=str(instance_dir / ".socket.sock"))
        await asyncio.start_unix_server(self._ev, path=str(instance_dir / ".socket2.sock"))

    async def _req(self, reader, writer):
        cmd = (await reader.read(1024)).decode()
        if cmd == "j/workspaces":
            writer.write(json.dumps(WORKSPACES).encode())
        elif cmd == "j/activeworkspace":
            writer.write(b'{"id": 3}')
        elif cmd == "j/activewindow":
            writer.write(b'{"class": "foot", "title": "hello"}')
        elif cmd == "j/clients":
            writer.write(b"[]")
        else:
            self.dispatched.append(cmd)
            writer.write(b"ok")
        await writer.drain()
        writer.close()

    async def _ev(self, reader, writer):
        self.event_writer = writer


@pytest.fixture
def pad():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _read_msgs(master):
    codec = km_proto.LineCodec()
    try:
        return codec.feed(os.read(master, 65536))
    except BlockingIOError:
        return []


def test_snapshot_on_connect_and_top_key_dispatch(pad, tmp_path):
    # Split deck: keys 0-5 are workspaces 1-6 again. A tap switches, a hold
    # moves the focused window there silently (rare and deliberate; logged).
    master, slave_path = pad

    async def scenario():
        hypr = FakeHypr()
        await hypr.start(tmp_path / "hypr" / "fake0")
        cfg = Config(device=slave_path, runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.4)
        msgs = _read_msgs(master)
        os.write(master, km_proto.encode({"t": "key", "n": 2, "act": "tap"}))
        os.write(master, km_proto.encode({"t": "key", "n": 0, "act": "hold"}))
        await asyncio.sleep(0.3)
        task.cancel()
        return msgs, hypr.dispatched, sup.state.active

    msgs, dispatched, active = asyncio.run(scenario())
    types = [m["t"] for m in msgs]
    for expected in ("hello", "ws", "win", "flags"):
        assert expected in types
    assert active == 3
    assert 'dispatch hl.dsp.focus({ workspace = "3" })' in dispatched
    assert 'dispatch hl.dsp.window.move({ workspace = "1", follow = false })' in dispatched


def test_bottom_key_tap_focuses_client_then_selects_tmux_window(monkeypatch, tmp_path):
    from operatord import tmux as tmuxmod
    calls = []

    async def fake_select(session, i):
        calls.append(("select", session, i))
        return True

    async def fake_dispatch(cmd):
        calls.append(("hypr", cmd))

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        sup = _supervisor(tmp_path)
        sup.link = _sent_link([])
        sup._dispatch = fake_dispatch
        sup.ctx_items = [
            {"id": "tmux:@1", "n": "1 rails", "s": "oracle", "i": 1,
             "addr": "0xfeet", "c": "c00", "active": False, "bell": False},
            {"id": "tmux:@2", "n": "2 logs", "s": "oracle", "i": 2,
             "addr": "0xfeet", "c": "c01", "active": False, "bell": False},
        ]
        sup.state.addr = "0xffox"                 # firefox holds focus
        sup._on_pad_msg({"t": "key", "n": 6, "act": "tap"})
        await asyncio.sleep(0.05)
        first = list(calls)
        calls.clear()
        sup.state.addr = "0xfeet"                 # associated terminal already focused
        sup._on_pad_msg({"t": "key", "n": 7, "act": "tap"})
        await asyncio.sleep(0.05)
        return first, list(calls)

    unfocused, focused = asyncio.run(scenario())
    # unfocused: focus the associated terminal FIRST, then select the tmux window
    assert unfocused == [("hypr", 'dispatch hl.dsp.focus({ window = "address:0xfeet" })'),
                         ("select", "oracle", 1)]
    assert focused == [("select", "oracle", 2)]   # no focus hop when already there


def test_bottom_key_tap_focuses_a_sessionless_terminal(monkeypatch, tmp_path):
    calls = []

    async def fake_dispatch(cmd):
        calls.append(cmd)

    async def scenario():
        sup = _supervisor(tmp_path)
        sup.link = _sent_link([])
        sup._dispatch = fake_dispatch
        sup.ctx_items = [{"id": "hypr:0xbare", "n": "bare", "addr": "0xbare",
                          "c": "c00", "active": False, "bell": False}]
        sup.state.addr = "0xffox"
        sup._on_pad_msg({"t": "key", "n": 6, "act": "tap"})
        await asyncio.sleep(0.05)
        return list(calls)

    assert asyncio.run(scenario()) == [
        'dispatch hl.dsp.focus({ window = "address:0xbare" })'
    ]


def test_bottom_key_edges_are_no_ops(monkeypatch, tmp_path):
    # An empty ctx slot, a bottom-half hold, and an out-of-range key must all
    # do nothing -- and must not crash the dispatcher.
    from operatord import tmux as tmuxmod
    selected = []

    async def fake_select(session, i):
        selected.append((session, i))
        return True

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        sup = _supervisor(tmp_path)
        sup.link = _sent_link([])
        sup.ctx_items = [{"id": "tmux:@1", "n": "1 rails", "s": "oracle", "i": 1,
                          "addr": "", "c": "c00", "active": False, "bell": False}]
        sup._on_pad_msg({"t": "key", "n": 7, "act": "tap"})    # empty slot
        sup._on_pad_msg({"t": "key", "n": 6, "act": "hold"})   # reserved: no-op
        sup._on_pad_msg({"t": "key", "n": 12, "act": "tap"})   # no such key
        sup._on_pad_msg({"t": "key", "n": -1, "act": "tap"})   # negative: ignored
        await asyncio.sleep(0.05)
        return selected

    assert asyncio.run(scenario()) == []


def test_link_up_replays_state_and_ctx(tmp_path):
    # A reconnecting pad (USB reset, firmware deploy) must not sit blank until
    # some unrelated event happens to change the computed messages.
    sup = _supervisor(tmp_path)
    sup.ctx_msg = {"t": "ctx", "items": [{"n": "1 rails", "c": "c00",
                                          "active": True, "bell": False}]}
    sent = []
    sup.link = _sent_link(sent)

    asyncio.run(sup._on_link_up())

    types = [m["t"] for m in sent]
    assert "ws" in types and "win" in types
    assert [m for m in sent if m.get("t") == "ctx"] == [sup.ctx_msg]


def test_link_up_before_first_poll_sends_no_ctx(tmp_path):
    sup = _supervisor(tmp_path)
    sent = []
    sup.link = _sent_link(sent)
    asyncio.run(sup._on_link_up())
    assert not any(m.get("t") == "ctx" for m in sent)


def test_link_drop_clears_ctx_msg_so_the_next_poll_resends(tmp_path):
    sup = _supervisor(tmp_path)
    sup.ctx_msg = {"t": "ctx", "items": []}
    sup._on_link_down()
    assert sup.ctx_msg is None


def test_dispatch_oserror_keeps_instance(tmp_path):
    # A transient IPC failure in _dispatch must NOT clear _instance:
    # only _hypr_events can rediscover it, and its trigger (socket2 death)
    # never fires on a healthy compositor — so clearing here permanently
    # killed the refresher (observed live 2026-08-18: frozen ws colors,
    # zero request-socket connects while events flowed).
    async def scenario():
        sup = _supervisor(tmp_path)
        dead = tmp_path / "hypr" / "gone"      # no .socket.sock -> OSError
        sup._instance = dead
        await sup._dispatch("dispatch workspace 2")
        assert sup._instance is dead           # still set; owner heals it
    asyncio.run(scenario())


def test_poll_ctx_builds_items_dedupes_and_reemits_on_change(monkeypatch, tmp_path):
    from operatord import hyprland as hyprmod
    from operatord import tmux as tmuxmod

    CLIENTS = [{"class": "kitty", "address": "0xaaa",
                "workspace": {"id": 1, "name": "mirepoix"}}]
    ASSOCIATIONS = [{"session": "mirepoix", "address": "0xaaa",
                     "workspace": {"id": 1}, "focusHistoryID": 0}]

    async def scenario():
        windows = [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
                    "active": True, "bell": False}]

        async def fake_list():
            return list(windows)

        async def fake_clients():
            return list(ASSOCIATIONS)

        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        monkeypatch.setattr(hyprmod, "led_palette", lambda: ["c00", "c01"])
        sup = _supervisor(tmp_path)
        sent = []
        sup.link = _sent_link(sent)
        sup.state.clients = CLIENTS
        sup.state.active = 1

        await sup._poll_ctx()
        after_first = list(sent)
        items_after_first = list(sup.ctx_items)

        await sup._poll_ctx()                     # identical input: no re-send
        after_second = list(sent)

        windows.clear()                            # the window disappears
        await sup._poll_ctx()
        after_third = list(sent)

        return after_first, after_second, after_third, items_after_first

    first, second, third, items = asyncio.run(scenario())
    ctx1 = [m for m in first if m["t"] == "ctx"]
    assert len(ctx1) == 1
    assert ctx1[0]["items"] == [{"n": "1 rails", "c": "c00",
                                 "active": True, "bell": False}]
    assert items[0]["s"] == "mirepoix"            # targets stay daemon-side
    assert items[0]["addr"] == "0xaaa"
    assert set(ctx1[0]["items"][0]) == {"n", "c", "active", "bell"}
    assert "s" not in ctx1[0]["items"][0]         # ...and off the wire
    assert len([m for m in second if m["t"] == "ctx"]) == 1   # deduped
    assert len([m for m in third if m["t"] == "ctx"]) == 2    # the change re-emits


def test_poll_ctx_before_first_hypr_snapshot_is_a_no_op(monkeypatch, tmp_path):
    from operatord import tmux as tmuxmod

    async def fake_list():
        return []

    async def fake_clients():
        return []

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        sup = _supervisor(tmp_path)
        sent = []
        sup.link = _sent_link(sent)
        sup.state.clients = []           # no snapshot yet
        await sup._poll_ctx()
        return sent

    assert asyncio.run(scenario()) == []


def test_poll_ctx_survives_a_tmux_outage_without_blanking_bare_terminals(monkeypatch, tmp_path):
    # list_deck_windows() returns None when the tmux SERVER is down, not when
    # it merely has no windows. A bare `foot` earns its key purely from
    # state.clients and has nothing to do with tmux's health.
    from operatord import hyprland as hyprmod
    from operatord import tmux as tmuxmod

    async def fake_list_none():
        return None

    async def fake_clients():
        return [{"session": "home", "address": "0xbare",
                 "workspace": {"id": 1}, "focusHistoryID": 0}]

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list_none)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        monkeypatch.setattr(hyprmod, "led_palette", lambda: ["c00"])
        sup = _supervisor(tmp_path)
        sent = []
        sup.link = _sent_link(sent)
        sup.state.clients = [{"class": "foot", "address": "0xbare",
                              "workspace": {"id": 1, "name": "home"}, "title": "bare"}]
        sup.state.active = 1
        sup.state._urgent_addrs = {"bare"}
        await sup._poll_ctx()
        return sent, sup.ctx_items

    sent, items = asyncio.run(scenario())
    ctx = [m for m in sent if m["t"] == "ctx"]
    assert len(ctx) == 1
    assert ctx[0]["items"] == [{"n": "bare", "c": "c00",
                                "active": False, "bell": True}]
    assert items[0]["id"] == "hypr:0xbare"


def test_poll_ctx_passes_an_ordinary_kitty_association(monkeypatch, tmp_path):
    from operatord import hyprland as hyprmod
    from operatord import tmux as tmuxmod

    async def fake_list():
        return [{"id": "tmux:@32", "s": "oracle", "i": 2, "n": "macropad",
                  "active": True, "bell": False}]

    async def fake_clients():
        return [{"session": "oracle", "address": "0xaa",
                 "workspace": {"id": 3}, "focusHistoryID": 0}]

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        monkeypatch.setattr(hyprmod, "led_palette", lambda: ["c00", "c01"])
        sup = _supervisor(tmp_path)
        sent = []
        sup.link = _sent_link(sent)
        sup.state.clients = [{"class": "kitty", "address": "0xaa",
                               "workspace": {"id": 3, "name": "oracle"}}]
        sup.state.active = 3
        await sup._poll_ctx()
        return sent, list(sup.ctx_items)

    sent, items = asyncio.run(scenario())
    (ctx,) = [m for m in sent if m["t"] == "ctx"]
    # c00 is colorhash("macropad") against this 2-cell fake palette.
    assert ctx["items"] == [{"n": "2 macropad", "c": "c00",
                             "active": True, "bell": False}]
    assert items[0]["addr"] == "0xaa"


def test_poll_ctx_successful_empty_associations_leave_terminal_sessionless(monkeypatch, tmp_path):
    from operatord import hyprland as hyprmod
    from operatord import tmux as tmuxmod

    async def fake_list():
        return [{"id": "tmux:@1", "s": "legacy", "i": 1, "n": "shell",
                 "active": True, "bell": False}]

    async def fake_clients():
        return []

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        monkeypatch.setattr(hyprmod, "led_palette", lambda: ["c00"])
        sup = _supervisor(tmp_path)
        sup.link = _sent_link([])
        sup.state.clients = [{"class": "ws-legacy", "address": "0xaaa",
                              "workspace": {"id": 1}, "title": "legacy"}]
        await sup._poll_ctx()
        return sup.ctx_items

    items = asyncio.run(scenario())
    assert [item["id"] for item in items] == ["hypr:0xaaa"]


def test_poll_ctx_logs_resolver_failure_once_per_outage_and_recovers(
        monkeypatch, tmp_path, capsys):
    from operatord import tmux as tmuxmod

    results = [None, None, [], None]

    async def fake_list():
        return []

    async def fake_clients():
        return results.pop(0)

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        sup = _supervisor(tmp_path)
        sup.state.clients = [{"class": "foot", "address": "0xaaa",
                              "workspace": {"id": 1}}]
        for _ in range(4):
            await sup._poll_ctx()
        return sup

    sup = asyncio.run(scenario())
    assert capsys.readouterr().out.splitlines() == [
        "operatord: tmux-local-clients failed",
        "operatord: tmux-local-clients failed",
    ]
    assert sup._resolver_failed is True


def test_poll_ctx_trims_names_for_the_wire(monkeypatch, tmp_path):
    # LineCodec discards over-long lines WHOLE, so an unbounded window name
    # could silently blank the pad. Names are trimmed daemon-side.
    from operatord import hyprland as hyprmod
    from operatord import tmux as tmuxmod

    async def fake_list():
        return [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "x" * 200,
                  "active": False, "bell": False}]

    async def fake_clients():
        return [{"session": "mirepoix", "address": "0xaaa",
                 "workspace": {"id": 1}, "focusHistoryID": 0}]

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        monkeypatch.setattr(tmuxmod, "list_local_clients", fake_clients)
        monkeypatch.setattr(hyprmod, "led_palette", lambda: ["c00"])
        sup = _supervisor(tmp_path)
        sent = []
        sup.link = _sent_link(sent)
        sup.state.clients = [{"class": "kitty", "address": "0xaaa",
                              "workspace": {"id": 1, "name": "mirepoix"}}]
        sup.state.active = 1
        await sup._poll_ctx()
        return sent

    sent = asyncio.run(scenario())
    (ctx,) = [m for m in sent if m["t"] == "ctx"]
    assert len(ctx["items"][0]["n"]) <= 20


def test_dial_previews_then_plays_once_and_push_stops(monkeypatch, tmp_path):
    sent, calls = [], []

    class FakeLofi:
        path = "fake"
        async def stations(self):
            return [{"id": "aaa", "title": "jazz"}, {"id": "bbb", "title": "sleep"}]
        async def status(self):
            return ("aaa", True)
        async def play(self, station_id):
            calls.append(("play", station_id))
        async def stop(self):
            calls.append(("stop",))

    async def scenario():
        sup = _supervisor(tmp_path)
        sup.link = _sent_link(sent)
        sup.lofi = FakeLofi()
        sup.tuner.settle_s = 0.1
        sup._on_pad_msg({"t": "dial", "d": 1})
        await asyncio.sleep(0.05)
        sup._on_pad_msg({"t": "dial", "d": 1})      # back to aaa: nothing to do
        await asyncio.sleep(0.2)
        first = (list(sent), list(calls))
        sup._on_pad_msg({"t": "dial", "d": 1})
        await asyncio.sleep(0.2)
        second = list(calls)
        sup._on_pad_msg({"t": "enc", "act": "tap"})
        await asyncio.sleep(0.05)
        return first, second, list(calls)

    (sent1, calls1), calls2, calls3 = asyncio.run(scenario())
    assert [m["title"] for m in sent1] == ["sleep", "jazz"]
    assert all(m["t"] == "tune" and m["line"] == "TUNING" for m in sent1)
    assert calls1 == []
    assert calls2 == [("play", "bbb")]
    assert calls3 == [("play", "bbb"), ("stop",)]
    assert [m["line"] for m in sent][2:] == ["TUNING", "LOADING", "STOPPING"]
    assert sent[-1]["title"] == "sleep"
