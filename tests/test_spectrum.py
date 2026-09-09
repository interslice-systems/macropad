import asyncio
import sys

import km_proto
from km_spectrum import BANDS, Spectrum, tile, visible
from operatord import spectrum


def msg(level, active=True):
    return {"t": "spectrum", "active": active, "bars": [level] * BANDS}


def test_peak_holds_then_falls_and_new_peak_restarts_hold():
    state = Spectrum(lambda a, b: a - b)
    state.receive(msg(12), 100)
    state.receive(msg(3), 150)
    state.advance(1100)
    assert state.peaks == [12] * BANDS
    state.advance(1180)
    assert state.peaks == [11] * BANDS
    state.receive(msg(14), 1200)
    state.receive(msg(2), 1250)
    state.advance(2200)
    assert state.peaks == [14] * BANDS
    state.advance(2360)
    assert state.peaks == [12] * BANDS


def test_silence_and_stale_frames_clear_display_and_resume_fresh():
    state = Spectrum(lambda a, b: a - b)
    state.receive(msg(16), 0)
    state.advance(1500)
    assert not state.active
    state.receive(msg(2), 1600)
    assert state.active and state.peaks == [2] * BANDS
    state.receive(msg(0, False), 1650)
    assert not state.active and state.peaks == [0] * BANDS


def test_tick_wraparound_keeps_hold_and_expiry_correct():
    diff = lambda a, b: (a - b + 32768) % 65536 - 32768
    state = Spectrum(diff)
    state.receive(msg(12), 65000)
    state.receive(msg(1), 65050)
    state.advance(464)   # 1000 ms after peak, across wrap
    assert state.active and state.peaks == [12] * BANDS
    state.advance(544)
    assert state.peaks == [11] * BANDS
    state.advance(1014)  # 1500 ms since last packet
    assert not state.active


def test_invalid_packets_do_not_extend_freshness():
    state = Spectrum(lambda a, b: a - b)
    state.receive(msg(8), 0)
    for bad in ({}, {**msg(1), "bars": [1]}, msg(17), msg(True),
                {**msg(1), "active": "yes"}):
        assert not state.receive(bad, 1000)
    state.advance(1500)
    assert not state.active


def test_segment_geometry_and_alert_priority():
    assert [tile(2, 4, r) for r in range(16)] == [0] * 12 + [2, 0, 1, 1]
    assert tile(16, 16, 0) == 3
    assert all(tile(0, 0, r) == 0 for r in range(16))
    assert visible(True, "calm", False)
    assert not visible(True, "ringing", True)
    assert not visible(True, "nolink", False)
    assert not visible(False, "calm", False)


def test_binary_chunks_keep_alignment_and_discard_old_complete_frames():
    frames = spectrum.Frames()
    assert frames.feed(bytes([255]) * 7, 0) is None
    assert frames.feed(bytes([255]) * 9 + bytes([128]) * 16 + b"\x00", 0) == msg(8)
    assert frames.feed(bytes(15), 1) == msg(0)
    assert frames.feed(bytes(16), 2) == msg(0, False)
    assert frames.feed(bytes([255]) * 16, 3) == msg(16)
    assert len(km_proto.encode(msg(16))) < 128


def test_analyzer_child_is_reaped_on_cancel():
    async def scenario():
        sent = []
        task = asyncio.create_task(spectrum.watch(
            sent.append, lambda: True,
            command=(sys.executable, "-c",
                     "import os,time; os.write(1, bytes([255])*16); time.sleep(60)")))
        try:
            async with asyncio.timeout(3):
                while not sent:
                    await asyncio.sleep(.01)
            assert sent[0] == msg(16)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert sent[-1] == spectrum.stopped()
    asyncio.run(scenario())


def test_analyzer_failure_returns_to_rain_and_retries(monkeypatch):
    monkeypatch.setattr(spectrum, "RETRY_S", .01)
    async def scenario():
        sent = []
        task = asyncio.create_task(spectrum.watch(
            sent.append, lambda: True, command=("/no-such-cava",)))
        try:
            async with asyncio.timeout(2):
                while len(sent) < 2:
                    await asyncio.sleep(.01)
            assert all(m == spectrum.stopped() for m in sent)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(scenario())


def test_disconnected_pad_does_not_launch_analyzer(monkeypatch):
    async def forbidden(*a, **kw):
        raise AssertionError("must not launch")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    async def scenario():
        task = asyncio.create_task(spectrum.watch(lambda m: None, lambda: False))
        await asyncio.sleep(.02)
        assert not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())
