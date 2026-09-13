"""Cockpit: the split deck. Keys 0-5 = workspaces 1-6 in their colorhash
colors; keys 6-11 = tmux windows associated with local terminals on the active
workspace, then unassociated terminals; OLED = weather display (rain / bell
wall / marquee, see docs/specs/2026-08-23-oled-weather-design.md)."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
import km_weather
from km_keys import KeyTracker

from pad.framework import App

KEYS = 12
BLINK_MS = 1000       # bell blink period; ~60 flashes/min, chosen on hardware


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.ws = {"active": 1, "occupied": [], "urgent": [], "colors": {},
                   "names": {}}
        self.ctx = {"t": "ctx", "items": []}
        # `win` and `ws["names"]` are accepted and ignored: the daemon still
        # sends both (the protocol is unchanged) but the weather display has
        # no consumer for either. Don't go hunting for one.
        self.win = {"cls": "", "title": ""}
        self.flags = {"submap": "", "screencast": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # Last frame actually written to the strip. `pixels.auto_write` stays True
        # (see framework.py) so every `pixels[i] = ...` drives the whole strip
        # immediately; writing only the pixels that changed keeps a static deck
        # at zero writes per tick instead of a clear-then-repaint strobe. None
        # forces a full first paint.
        self._led_frame = [None] * KEYS
        # Stamps a workspace on first sight of its bell (km_weather), keyed by
        # workspace int; drives the bell-wall recency order.
        self._stamps = {}
        self._last_active = None

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # A ledtest repaints the strip behind our back; a stale cache would
        # leave that debug frame stuck instead of being redrawn. The OLED
        # needs no equivalent reset -- ledtest never touches the panel, so
        # the Screen's caches still describe what is on it.
        self._led_frame = [None] * KEYS
        self._draw_all(ticks_ms())

    def on_msg(self, msg):
        t = msg["t"]
        if t == "media":
            self.screen.set_media(msg, ticks_ms())
            return
        elif t == "spectrum":
            self.screen.set_spectrum(msg, ticks_ms())
            return     # twenty audio frames/s must not repaint the key deck
        elif t == "tune":
            self.screen.set_tune(msg, ticks_ms())
            return
        if t == "ws":
            self.ws = msg
        elif t == "ctx":
            self.ctx = msg
        elif t == "win":
            self.win = msg
        elif t == "flags":
            self.flags = msg
        elif t == "palette":
            self.palette = msg
        self._draw_all(ticks_ms())

    def on_key_event(self, n, pressed, now):
        if pressed:
            self.tracker.press(n, now)
        elif self.tracker.release(n, now) == "tap":
            self.link.send({"t": "key", "n": n, "act": "tap"})

    def on_dial(self, delta):
        # The knob is the host's: it browses Lofi Girl stations (operatord/lofi.py).
        self.link.send({"t": "dial", "d": delta})

    def on_enc(self, pressed, now):
        if not pressed:
            self.link.send({"t": "enc", "act": "tap"})

    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._sync_screen(now)        # cheap: every setter diffs internally
        self.screen.tick(now)         # rain + marquee frame clock

    # ---- drawing --------------------------------------------------
    def _ws_state(self, n):
        ws = n + 1
        if ws == self.ws["active"]:
            return "active"
        if ws in self.ws["urgent"]:
            return "urgent"
        if ws in self.ws["occupied"]:
            return "occupied"
        return "empty"

    def _draw_leds(self, now):
        # Linear ramp, NOT a triangle: the bell blink is a square wave and
        # needs to know where it is in the cycle, not how far from the ends.
        # (The `% BLINK_MS` glitches once per ticks_ms wrap, every ~6 days --
        # one cycle lands short. Pre-existing, invisible, not worth a
        # wraparound-safe epoch here.)
        phase = (now % BLINK_MS) / BLINK_MS
        frame = [0x000000] * KEYS
        if self.link.up:
            colors = self.ws.get("colors", {})
            for n in range(6):
                frame[n] = km_palette.ws_key_color(
                    self._ws_state(n), colors.get(str(n + 1)), self.palette, phase)
            items = self.ctx.get("items", [])
            for n in range(6, KEYS):
                item = items[n - 6] if n - 6 < len(items) else None
                frame[n] = km_palette.ctx_key_color(item, self.palette, phase)
        # auto_write is True (framework.py), so every pixel assignment drives
        # the whole strip immediately. Writing only the pixels that changed
        # keeps a static deck at zero writes per tick and stops the
        # clear-then-repaint pass from strobing the hardware.
        for i, c in enumerate(frame):
            if c != self._led_frame[i]:
                self.pad.pixels[i] = c
        self._led_frame = frame

    def _sync_screen(self, now):
        if not self.link.up:
            self._stamps.clear()
            self._last_active = None
            self.screen.set_weather("nolink")
            self.screen.set_flags(False, "")
            return
        urgent = self.ws.get("urgent") or []   # None-proof against bad msgs
        km_weather.update_stamps(self._stamps, urgent, now)
        # set_bells BEFORE set_weather: the wall must be populated before the
        # weather flip reveals it, or the panel can scan out an empty frame.
        self.screen.set_bells(km_weather.bell_order(self._stamps, ticks_diff))
        # link_up=True is a literal, not an oversight: the link-down case
        # returned above, so by here the link is up by construction.
        self.screen.set_weather(km_weather.weather(urgent, True))
        self.screen.set_flags(bool(self.flags.get("screencast")),
                              self.flags.get("submap") or "")
        active = self.ws.get("active")
        if (active is not None and self._last_active is not None
                and active != self._last_active):
            self.screen.marquee(active)
        if active is not None:
            self._last_active = active

    def _draw_all(self, now):
        self._draw_leds(now)
        self._sync_screen(now)
