"""The knob push as a radio power button for the interslice.lofi bar widget.

`lofi toggle` owns the whole decision -- stop if running, else start the
last-played stream (or the first) -- so the daemon keeps no station state.
It only asks `lofi status` first, to put the right line on the faceplate.
Station browsing left the knob 2026-09-15: detent-by-detent tuning was slow,
and the media keys (MPRIS Next/Previous) already step between streams.
"""
import asyncio
import json
import os
import shutil

from . import media

NOTICE_S = 10       # LOADING: cleared early by the player's new title
STOP_S = 4
PLUGIN_BIN = os.path.expanduser("~/.config/omarchy/plugins/interslice.lofi/bin/lofi")


def find_script():
    return shutil.which("lofi") or PLUGIN_BIN


def notice(title, line, hold=NOTICE_S):
    """A faceplate line: the station on top, what the push is doing below."""
    return {"t": "tune", "title": media.clean(title), "line": line, "hold": hold}


def push_notice(status):
    """The faceplate line for a push, given (id, running, title) before it."""
    _, running, title = status
    if running:
        return notice(title or "LOFI GIRL", "STOPPING", STOP_S)
    return notice("LOFI GIRL", "LOADING")


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

    async def status(self):
        try:
            data = json.loads(await self._run("status") or "{}")
            return (str(data.get("id") or ""), bool(data.get("running")),
                    str(data.get("title") or ""))
        except ValueError:
            return ("", False, "")

    async def toggle(self):
        await self._run("toggle")
