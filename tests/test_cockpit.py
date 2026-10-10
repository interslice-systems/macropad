"""Cockpit key-state decisions, run on CPython with the CircuitPython imports
stubbed (same approach as test_spectrum_ui.py). Covers what km_palette's tests
cannot: which state a KEY is in, and that a message from an older daemon is
still drawable."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

import km_palette as kp

HUE = "e16000"
C = kp.hex_to_int(HUE)


class _Pixels:
    def __init__(self):
        self.px = [None] * 12

    def __setitem__(self, i, v):
        self.px[i] = v


@pytest.fixture
def cockpit(monkeypatch):
    monkeypatch.setitem(sys.modules, "adafruit_ticks", types.SimpleNamespace(
        ticks_ms=lambda: 0, ticks_diff=lambda a, b: a - b))
    pad = types.ModuleType("pad")
    framework = types.ModuleType("pad.framework")
    framework.App = object
    monkeypatch.setitem(sys.modules, "pad", pad)
    monkeypatch.setitem(sys.modules, "pad.framework", framework)
    spec = importlib.util.spec_from_file_location(
        "cockpit_test", Path(__file__).parents[1] / "firmware/apps/cockpit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = mod.Cockpit()
    app.link = types.SimpleNamespace(up=True)
    app.pad = types.SimpleNamespace(pixels=_Pixels())
    return app


def _ws(**kw):
    msg = {"t": "ws", "active": 1, "occupied": [1, 2, 3], "urgent": [],
           "blocked": [], "colors": {"1": HUE, "2": HUE, "3": HUE}, "names": {}}
    msg.update(kw)
    return msg


def test_a_blocked_workspace_breathes_and_outranks_a_bell(cockpit):
    cockpit.ws = _ws(urgent=[2], blocked=[2])
    cockpit._draw_leds(0)                        # breath trough, blink lit
    assert cockpit.pad.pixels.px[1] == kp.scale(C, kp.BLOCKED_FLOOR)
    cockpit._draw_leds(kp.BLOCKED_PERIOD_MS // 2)   # breath peak
    assert cockpit.pad.pixels.px[1] == C


def test_a_blocked_active_workspace_still_breathes(cockpit):
    # Being on the workspace is not having answered the prompt.
    cockpit.ws = _ws(active=1, blocked=[1])
    cockpit._draw_leds(0)
    assert cockpit.pad.pixels.px[0] == kp.scale(C, kp.BLOCKED_FLOOR)


def test_unblocked_keys_render_as_before(cockpit):
    cockpit.ws = _ws(active=1, urgent=[3], blocked=[])
    cockpit._draw_leds(250)                      # blink lit at phase 0.25
    assert cockpit.pad.pixels.px[0] == C                          # active
    assert cockpit.pad.pixels.px[1] == kp.scale(C, kp.OCCUPIED_SCALE)
    assert cockpit.pad.pixels.px[2] == C                          # urgent, lit half


def test_a_ws_message_from_an_older_daemon_has_no_blocked_key(cockpit):
    old = _ws()
    del old["blocked"]
    cockpit.ws = old
    cockpit._draw_leds(0)                        # must not raise
    assert cockpit.pad.pixels.px[0] == C


def test_a_blocked_window_key_breathes(cockpit):
    cockpit.ws = _ws()
    cockpit.ctx = {"t": "ctx", "items": [
        {"n": "1 agent", "c": HUE, "active": True, "bell": True, "blocked": True},
        {"n": "2 logs", "c": HUE, "active": False, "bell": False},   # old-daemon item
    ]}
    cockpit._draw_leds(0)
    assert cockpit.pad.pixels.px[6] == kp.scale(C, kp.BLOCKED_FLOOR)
    assert cockpit.pad.pixels.px[7] == kp.scale(C, kp.OCCUPIED_SCALE)
