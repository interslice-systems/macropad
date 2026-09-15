"""Supervised CAVA -> compact spectrum frames. Audio never enters the pad."""
import asyncio
import logging
import time
from pathlib import Path

from km_spectrum import BANDS, LEVELS

CONFIG = Path(__file__).with_name("cava.conf")
SILENCE_S = 2.0
RETRY_S = 5.0


class Frames:
    """Retain a partial binary frame; drop old complete frames on a backlog."""
    def __init__(self):
        self.pending = bytearray()
        self.audible = None

    def feed(self, data, now):
        self.pending.extend(data)
        complete = len(self.pending) // BANDS * BANDS
        if not complete:
            return None
        raw = self.pending[complete - BANDS:complete]
        del self.pending[:complete]
        bars = [v * LEVELS // 255 for v in raw]
        if any(bars):
            self.audible = now
        active = self.audible is not None and now - self.audible < SILENCE_S
        return {"t": "spectrum", "active": active, "bars": bars}


def stopped():
    return {"t": "spectrum", "active": False, "bars": [0] * BANDS}


async def _stop(proc):
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


async def watch(send, connected, command=("cava",)):
    """An analyzer failure never stops key handling; reconnect starts fresh."""
    failed = False
    while True:
        if not connected():
            await asyncio.sleep(1)
            continue
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, "-p", str(CONFIG), stdout=asyncio.subprocess.PIPE,
                # Errors go to the user service journal, not an undrained pipe.
                stderr=None)
            frames = Frames()
            last = None
            while connected():
                data = await asyncio.wait_for(proc.stdout.read(4096), 3)
                if not data:
                    raise RuntimeError("CAVA output closed")
                msg = frames.feed(data, time.monotonic())
                if msg is None:
                    continue
                # Live frames double as freshness heartbeats. Idle is one
                # transition, not twenty redundant USB writes per second.
                if msg["active"] or msg != last:
                    send(msg)
                last = msg
                if failed:
                    logging.info("macropadd: spectrum recovered")
                failed = False
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            if not failed:
                logging.warning("macropadd: spectrum unavailable: %s", exc)
            failed = True
        finally:
            send(stopped())
            if proc is not None:
                await _stop(proc)
        await asyncio.sleep(RETRY_S)
