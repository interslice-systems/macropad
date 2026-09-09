"""operatord: supervises serial link, Hyprland stream, theme watcher."""
import asyncio
import json
import os

from dataclasses import dataclass
from pathlib import Path

from . import hyprland, ledtest, media, spectrum, tmux
from .serial_link import SerialLink
from .theme import ThemeWatcher

PING_S = 5.0
DEBOUNCE_S = 0.1
CTX_POLL_S = 1.0

# LineCodec discards lines over 1024 bytes WHOLE, so an unbounded window name
# would silently blank the pad rather than truncate. Six items at this cap plus
# JSON overhead sits far under the limit.
CTX_NAME_MAX = 20


@dataclass
class Config:
    device: str = os.environ.get("OPERATOR_DEVICE", "/dev/operator-data")
    runtime_dir: Path = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    home: Path = Path.home()
    state_dir: Path = Path(os.environ.get("OPERATOR_STATE_DIR")
                           or Path.home() / ".local/state/operator")


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = hyprland.HyprState()
        self.palette = None
        self.media_msg = media.empty()
        self.spectrum_msg = spectrum.stopped()
        # The bottom deck. ctx_items keeps the full items (jump targets
        # included) for tap resolution; ctx_msg is the last WIRE message, kept
        # separately so dedup compares what the pad actually saw. Empty/None
        # until the first poll lands, so an early tap is a no-op rather than an
        # IndexError.
        self.ctx_items = []
        self.ctx_msg = None
        self.link = SerialLink(cfg.device, on_msg=self._on_pad_msg,
                               on_up=self._on_link_up, on_down=self._on_link_down)
        self._refresh_wanted = asyncio.Event()
        self._instance = None
        self._tasks = set()
        self._resolver_failed = False

    def _spawn(self, coro, what):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def _done(t):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                print(f"operatord: {what} failed: {t.exception()!r}", flush=True)

        task.add_done_callback(_done)

    # ---- outbound -------------------------------------------------
    def _flags_msg(self):
        return {"t": "flags", "submap": self.state.submap,
                "screencast": self.state.screencast}

    async def _on_link_up(self):
        self.link.send({"t": "hello", "host": "operatord", "proto": 1})
        if self.palette:
            self.link.send(self.palette)
        for m in self.state.snapshot():
            self.link.send(m)
        # A reconnecting pad (USB reset, a firmware deploy, which also re-sends
        # `hello`) otherwise starts from an empty bottom deck and stays that
        # way until some unrelated window change beats _poll_ctx's dedup guard.
        # None only before the first poll has ever landed; nothing to send yet.
        if self.ctx_msg is not None:
            self.link.send(self.ctx_msg)
        self.link.send(self._flags_msg())
        self.link.send(self.spectrum_msg)
        self.link.send(self.media_msg)

    def _on_media(self, msg):
        self.media_msg = msg
        self.link.send_frame(msg)

    def _on_spectrum(self, msg):
        self.spectrum_msg = msg
        self.link.send_frame(msg)

    def _on_link_down(self):
        # Force the next poll to re-emit even if nothing about the windows has
        # changed -- see _on_link_up's resend above for why a dedup-suppressed
        # ctx otherwise leaves a reconnected pad blank.
        self.ctx_msg = None
        self.spectrum_msg = spectrum.stopped()
        self.media_msg = media.empty()

    async def _on_palette(self, pal):
        # Store and forward. self.palette is kept because _on_link_up re-sends
        # it: a pad that reconnects mid-session has no palette until the next
        # theme change.
        self.palette = pal
        self.link.send(pal)

    # ---- inbound from pad -----------------------------------------
    def _on_pad_msg(self, msg):
        t = msg.get("t")
        if t == "hello":
            self._spawn(self._on_link_up(), "link-up")
        elif t == "key":
            n = int(msg.get("n", 0))
            if 0 <= n < 6:                                # top half: workspaces 1-6
                workspace = n + 1
                if msg.get("act") == "hold":
                    # Holds are rare and destructive; log for forensics.
                    print(f"operatord: hold key {n} -> movetoworkspacesilent {workspace}",
                          flush=True)
                    cmd = (f'dispatch hl.dsp.window.move({{ workspace = "{workspace}", '
                           'follow = false })')
                else:
                    cmd = f'dispatch hl.dsp.focus({{ workspace = "{workspace}" }})'
                self._spawn(self._dispatch(cmd), "dispatch")
            elif 6 <= n <= 11 and msg.get("act") == "tap":
                self._spawn(self._activate_item(n - 6), "ctx-activate")

    async def _dispatch(self, cmd):
        # Swallow transient IPC failures WITHOUT clearing _instance:
        # _hypr_events owns that handle (it rediscovers after its socket2
        # connection dies, which is what a real Hyprland restart looks like).
        # Clearing it here permanently disabled the refresher, since nothing
        # else could ever set it again (live incident 2026-08-18).
        if self._instance is not None:
            try:
                await hyprland.request(self._instance, cmd)
            except OSError as e:
                print(f"operatord: dispatch failed ({e!r}): {cmd}", flush=True)

    async def _activate_item(self, offset):
        """Tap on a bottom key: focus the item's Hyprland client if it isn't
        focused (the deck is workspace-aware, not focus-gated), then select the
        tmux window inside it where there is one. The items list is a snapshot
        taken at tap time so a poll racing this coroutine cannot swap the
        target under it."""
        items = self.ctx_items
        if not 0 <= offset < len(items):
            return
        item = items[offset]
        addr = item.get("addr")
        if addr and addr != self.state.addr:
            await self._dispatch(
                f'dispatch hl.dsp.focus({{ window = "address:{addr}" }})')
        if "s" in item:
            if not await tmux.select_window(item["s"], item["i"]):
                print(f"operatord: select-window {item['s']}:{item['i']} failed",
                      flush=True)

    # ---- hyprland side --------------------------------------------
    async def _hypr_events(self):
        while True:
            self._instance = hyprland.find_instance_dir(self.cfg.runtime_dir)
            if self._instance is None:
                await asyncio.sleep(2)
                continue
            try:
                reader, _ = await asyncio.open_unix_connection(
                    str(self._instance / ".socket2.sock"))
                self._refresh_wanted.set()
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    ev = hyprland.parse_event(line.decode(errors="replace").rstrip("\n"))
                    if ev is None:
                        continue
                    needs_refresh, flags_changed = self.state.handle_event(*ev)
                    if needs_refresh:
                        self._refresh_wanted.set()
                    if flags_changed:
                        self.link.send(self._flags_msg())
            except OSError:
                pass
            await asyncio.sleep(2)   # hyprland restarting; rediscover

    async def _refresher(self):
        while True:
            await self._refresh_wanted.wait()
            await asyncio.sleep(DEBOUNCE_S)               # coalesce bursts
            self._refresh_wanted.clear()
            if self._instance is None:
                continue
            try:
                ws = json.loads(await hyprland.request(self._instance, "j/workspaces"))
                aw = json.loads(await hyprland.request(self._instance, "j/activeworkspace"))
                win_raw = await hyprland.request(self._instance, "j/activewindow")
                win = json.loads(win_raw) if win_raw.strip() not in (b"", b"{}") else None
                clients = json.loads(await hyprland.request(self._instance, "j/clients"))
            except (OSError, ValueError):
                continue
            for m in self.state.refresh(ws, aw, win, clients):
                self.link.send(m)

    async def _pinger(self):
        while True:
            await asyncio.sleep(PING_S)
            self.link.send({"t": "ping"})

    async def _poll_loop(self):
        while True:
            await asyncio.sleep(CTX_POLL_S)
            await self._poll_ctx()

    async def _poll_ctx(self):
        # The bottom deck is WORKSPACE-aware, not focus-gated: keys 6-11 track
        # the windows living on the active workspace whether or not one of them
        # holds focus, so the deck stays lit while you're in the browser next
        # to it.
        try:
            if not self.state.clients:
                return          # no Hyprland snapshot yet; a wipe here would be a lie
            twins = await tmux.list_deck_windows()
            tmux_available = twins is not None
            if twins is None:
                # tmux unavailable (server down) is NOT "tmux has no windows":
                # a bare terminal earns a key purely from state.clients and has
                # nothing to do with tmux's health. Continue with an empty tmux
                # side so the sessionless keys still render.
                twins = []
            associations = await tmux.list_local_clients()
            if associations is None:
                if not self._resolver_failed:
                    print("operatord: tmux-local-clients failed", flush=True)
                self._resolver_failed = True
                associations = []
            else:
                self._resolver_failed = False

            # Without authoritative tmux windows, treat terminals as sessionless
            # and retain coarse bells rather than hiding their only alert.
            effective_associations = associations if tmux_available else []
            bells = hyprland.deck_bells(twins, self.state._urgent_addrs,
                                        effective_associations)
            items = hyprland.ctx_windows(
                twins, self.state.clients, effective_associations, self.state.active,
                hyprland.led_palette(), focused_addr=self.state.addr, bells=bells)
            self.ctx_items = items
            msg = {"t": "ctx", "items": [
                {"n": it["n"][:CTX_NAME_MAX], "c": it["c"],
                 "active": it["active"], "bell": it["bell"]} for it in items]}
            if msg != self.ctx_msg:
                self.ctx_msg = msg
                self.link.send(msg)
        except Exception as e:
            print(f"operatord: ctx poll failed: {e!r}", flush=True)

    async def run(self):
        theme = ThemeWatcher(self.cfg.home, self._on_palette)
        # ledtest: spike-grade debug bridge for palette eyeballing. Always via
        # cfg.state_dir so test_supervisor.py's tmp_path stays hermetic; the
        # watcher idles harmlessly when the spool never appears.
        await asyncio.gather(self.link.run(), self._hypr_events(),
                             self._refresher(), self._pinger(),
                             self._poll_loop(), theme.run(),
                             media.watch(self._on_media, lambda: self.link.up),
                             spectrum.watch(self._on_spectrum, lambda: self.link.up),
                             ledtest.watch(str(self.cfg.state_dir / "ledtest.json"),
                                           self.link.send))


def main():
    asyncio.run(Supervisor(Config()).run())


if __name__ == "__main__":
    main()
