"""The knob as a volume knob.

Each detent is STEP percent through omarchy-audio-output-volume, the same
script the keyboard's volume keys run, so the pad resolves the same physical
sink and shows the same OSD.

That script reads the volume, adds, then writes. Two of those racing lose a
step, and a quick spin sends detents faster than one invocation finishes. So
detents accumulate in `pending` and a single drain task runs them one
invocation at a time: however fast the knob turns, nothing is lost and at most
one command is in flight.
"""
import asyncio
import shutil

STEP = 2            # percent per detent; the keyboard keys move 5
SCRIPT = "omarchy-audio-output-volume"


class Knob:
    def __init__(self, run=None):
        self._run = run or _run_script
        self.pending = 0
        self._drain = None

    def turn(self, delta):
        self.pending += delta
        if self._drain is None or self._drain.done():
            self._drain = asyncio.ensure_future(self._drain_pending())
        return self._drain

    async def _drain_pending(self):
        while self.pending:
            step, self.pending = self.pending * STEP, 0
            await self._run("%+d" % step)


async def _run_script(arg):
    path = shutil.which(SCRIPT)
    if path is None:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            path, arg, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 5)
    except (OSError, asyncio.TimeoutError):
        pass
