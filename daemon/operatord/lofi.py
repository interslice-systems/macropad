"""The knob as a station dial for the interslice.lofi bar widget.

Tuner is the pure state machine (unit-tested with a fake clock); Client is
the thin shell-out to bin/lofi. Turning browses the live station list and,
while something is playing, commits the switch once the knob rests. While
stopped, turning only previews; the push starts the cursor station or stops
the player, because the push is awkward and start/stop are rare.
"""
import asyncio
import json
import os
import shutil

from . import media

HOLD_S = 3          # a preview while turning
NOTICE_S = 10       # LOADING: cleared early by the player's new title
PLUGIN_BIN = os.path.expanduser("~/.config/omarchy/plugins/interslice.lofi/bin/lofi")


def find_script():
    return shutil.which("lofi") or PLUGIN_BIN


class Tuner:
    def __init__(self, settle_s=0.4, resync_s=5.0):
        self.settle_s = settle_s
        self.resync_s = resync_s
        self.stations = []
        self.cursor = None
        self.origin = None       # cursor at sync; a settle back here is a no-op
        self.running = False
        self.deadline = None
        self.last_dial = None

    def needs_sync(self, now):
        return self.last_dial is None or now - self.last_dial >= self.resync_s

    def sync(self, stations, current_id, running):
        self.stations = list(stations)
        self.running = running
        ids = [s.get("id") for s in self.stations]
        self.cursor = ids.index(current_id) if current_id in ids else (0 if ids else None)
        self.origin = self.cursor

    def notice(self, line, hold=NOTICE_S):
        """A faceplate line about the cursor station: TUNING, LOADING, STOPPING."""
        if self.cursor is None:
            return None
        return {"t": "tune", "title": media.clean(self.stations[self.cursor].get("title", "")),
                "line": line, "hold": hold}

    def dial(self, delta, now):
        self.last_dial = now
        if self.cursor is None:
            return None
        self.cursor = (self.cursor + delta) % len(self.stations)
        self.deadline = now + self.settle_s
        return self.notice("TUNING" if self.running else "PUSH TO PLAY", HOLD_S)

    def settle(self, now):
        if self.deadline is None or now < self.deadline:
            return None
        self.deadline = None
        if not self.running or self.cursor == self.origin:
            return None
        self.origin = self.cursor
        return self.stations[self.cursor]["id"]

    def push(self):
        if self.running:
            return ("stop", None)
        if self.cursor is None:
            return ("play", None)
        return ("play", self.stations[self.cursor]["id"])


class Client:
    def __init__(self, path=None):
        self.path = path or find_script()

    async def _run(self, *args):
        try:
            proc = await asyncio.create_subprocess_exec(
                self.path, *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 20)
            return out.decode() if proc.returncode == 0 else ""
        except (OSError, asyncio.TimeoutError):
            return ""

    async def stations(self):
        try:
            data = json.loads(await self._run("list") or "[]")
        except ValueError:
            return []
        return [s for s in data if isinstance(s, dict) and isinstance(s.get("id"), str)]

    async def status(self):
        try:
            data = json.loads(await self._run("status") or "{}")
            return (str(data.get("id") or ""), bool(data.get("running")))
        except ValueError:
            return ("", False)

    async def play(self, station_id):
        await self._run("play", station_id)

    async def stop(self):
        await self._run("stop")
