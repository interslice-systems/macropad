"""The knob as a volume knob: detents coalesce, one command at a time."""
import asyncio

from macropadd import volume


def test_a_quick_spin_loses_no_detents_and_never_overlaps_commands():
    calls, inflight = [], []

    async def run(arg):
        inflight.append(1)
        assert len(inflight) == 1
        calls.append(arg)
        await asyncio.sleep(0.02)
        inflight.pop()

    async def scenario():
        knob = volume.Knob(run=run)
        knob.turn(1)
        for d in (1, 1, -1, 1):
            await asyncio.sleep(0.001)
            knob.turn(d)
        await asyncio.sleep(0.1)
        knob.turn(-1)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert sum(int(c) for c in calls) == (1 + 1 + 1 - 1 + 1 - 1) * volume.STEP
    assert calls[0] == "+2"
    assert calls[-1] == "-2"


def test_detents_that_cancel_out_run_nothing():
    calls = []

    async def run(arg):
        calls.append(arg)

    async def scenario():
        knob = volume.Knob(run=run)
        knob.pending = 1
        knob.turn(-1)
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert calls == []
