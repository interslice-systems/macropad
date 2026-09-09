"""OLED weather: layered scene graph -- rain / bell wall / marquee / badges.

All decisions live in km_weather (host-tested); this file only translates
its outputs into displayio mutations. Discipline per docs/pad-timing.md
section 5: every write path diffs against what was last written; hidden or
static layers cost zero work; no live bitmap is ever cleared wholesale.

Layer order, bottom to top (see Screen.__init__):

  0  rain     the rain TileGrid
  1  wall     the bell wall's numerals (shown only in "ringing")
  2  marquee  the workspace-switch numeral, held centred for MARQUEE_MS
  3  badges   REC + [submap] + the "no link" tag; topmost, composited last

The "no link" tag rides in the badge layer per the design spec section 4.4:
that layer's semantics are "alarms that stay visible over everything", and a
dead link is one. It is still driven purely by set_weather. (In practice the
marquee can never cover it anyway -- a marquee needs a live link to fire.)

Every layer's palette makes color 0 transparent, so the layers composite
rather than occlude: the marquee sits over whatever base state is live,
and the badges sit on top of everything.

Layer *visibility* is derived, never assigned ad hoc: `_sync_layers` is the
single place that decides which of rain/wall/no-link is showing, from
`self._weather` and whether the wall has anything in it. That is what makes
the set_weather/set_bells ordering contract safe -- a caller that unhides
"ringing" before the wall is populated gets rain, not a blank panel, and
`set_bells` unhides the wall itself once it has numerals to show. Callers
should still prefer set_bells-then-set_weather; the derivation is the
belt to that suspenders.

This module is not host-testable (displayio is firmware-only). It is
therefore written to be mechanical and obvious; anything non-local is
commented rather than left for a reader to re-derive.
"""
import random

import displayio
import terminalio
from adafruit_display_text import label
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

import km_weather
import km_spectrum
import km_stereo
from assets.digits import DIGITS

# Back to the spec's 100/2 after the bench pass. These briefly ran at 50/4,
# which bought the sliding marquee 12 steps instead of 6; the slide is gone
# (it tore), and rain is now the only thing on this clock. Both pairs give
# the same 200ms rain step, so 100/2 is the same picture for half the gate
# evaluations. Change them together or not at all: moving FRAME_MS alone
# silently halves or doubles the rain.
FRAME_MS = 100               # animation clock, 10 fps
RAIN_DIV = 2                 # rain advances every 2nd frame (~5 fps)
SPECTRUM_FRAME_MS = 50       # independent 20 fps clock; rain stays at 5 fps
RAIN_GLYPHS = "01<>=+*:#$KMTXZ7"          # 16 glyphs, matrix-flavored

_KIND_BANK = {"head": 0, "dim": 1}        # tile-sheet banks; "off" = blank

# Right-anchored badge geometry, in terminalio's 6px cells.
_BADGE_CELL = 6
_REC_TEXT = " REC "
_SUBMAP_RIGHT = km_weather.SCREEN_W - len(_REC_TEXT) * _BADGE_CELL - 4
# " [name] " is len(name)+4 cells wide and must not run off the left edge.
_SUBMAP_MAX = _SUBMAP_RIGHT // _BADGE_CELL - 4


def _mono_palette():
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0xFFFFFF
    pal.make_transparent(0)   # layers composite over each other
    return pal


def _set_hidden(obj, value):
    """Assign .hidden only on change.

    displayio's hidden setter is not documented to short-circuit, and a
    redundant assignment that marks the group dirty costs a panel refresh
    (docs/pad-timing.md section 5). Reading the attribute is free.
    """
    if obj.hidden != value:
        obj.hidden = value


def _digit_bitmap(rows, scale):
    """Build a 1-bit Bitmap from 14-wide row masks, nearest-neighbor scaled."""
    w, h = km_weather.SMALL_W * scale, km_weather.SMALL_H * scale
    bmp = displayio.Bitmap(w, h, 2)
    for r, mask in enumerate(rows):
        for c in range(km_weather.SMALL_W):
            if mask >> c & 1:
                for dr in range(scale):
                    for dc in range(scale):
                        bmp[c * scale + dc, r * scale + dr] = 1
    return bmp


def _fill_rain_sheet(sheet, font, gw, gh, n):
    """Copy each RAIN_GLYPHS glyph into the bright and dim banks.

    This is the part that reaches into `terminalio.FONT`'s internals:
    `glyph.bitmap` is assumed to be the font's shared sheet (not a per-glyph
    bitmap), `glyph.tile_index` to index into it, and the sheet's width to be
    an exact multiple of the bounding box width. Those hold for `BuiltinFont`
    on the builds we ship against, but they are C-module implementation
    details rather than documented API -- hence the caller's guard.
    """
    for i, ch in enumerate(RAIN_GLYPHS):
        glyph = font.get_glyph(ord(ch))
        if glyph is None:
            continue          # missing glyph degrades to a blank tile
        # Locate this glyph's tile from tile_index against the sheet's real
        # geometry -- the layout (strip vs grid) is an implementation detail
        # we must not assume.
        src = glyph.bitmap
        per_row = src.width // gw
        sx = (glyph.tile_index % per_row) * gw
        sy = (glyph.tile_index // per_row) * gh
        for y in range(gh):
            for x in range(gw):
                if src[sx + x, sy + y]:
                    sheet[(1 + i) * gw + x, y] = 1              # bright bank
                    # gw is even, so this checkerboard's phase is identical
                    # in every tile and the dither stays coherent on screen.
                    if (x + y) % 2 == 0:
                        sheet[(1 + n + i) * gw + x, y] = 1      # dim bank


def _rain_sheet(font):
    """Tile sheet from terminalio glyphs: bank 0 bright, bank 1 dimmed by a
    checkerboard mask (the 1-bit stand-in for 50% gray). Built once at boot.

    Tile 0 is a deliberate blank so the "off" kind is an ordinary tile write
    rather than a special case -- nothing ever clears a live bitmap here.

    Degrades rather than raises: if the font internals `_fill_rain_sheet`
    relies on differ on this build, the sheet is returned all-blank and the
    rain is silently disabled (a REPL line says so). Everything else on the
    panel keeps working. This is deliberately unlike a missing digit asset,
    which fails loud per the design spec section 7 -- a committed asset going
    missing is a deploy bug, whereas font internals varying by build is a
    runtime condition, and `Screen()` is constructed outside every `try` in
    `pad/framework.py`, so raising here means no OLED, no LEDs, no keys and
    no link until the pad is re-deployed. A rainless pad that still answers
    "what is ringing right now" beats a dark one.
    """
    box = font.get_bounding_box()
    gw, gh = box[0], box[1]
    n = len(RAIN_GLYPHS)
    sheet = displayio.Bitmap(gw * (1 + 2 * n), gh, 2)
    try:
        _fill_rain_sheet(sheet, font, gw, gh, n)
    except Exception as err:      # noqa: BLE001 -- bare-ish on purpose, above
        # A partly-filled sheet is fine: every tile is independent and an
        # unwritten one is simply blank. No re-allocation, no retry.
        print("rain disabled: terminalio glyph layout unexpected:", err)
    return sheet, gw, gh, n


class Screen:
    def __init__(self, display):
        self._display = display
        self.group = displayio.Group()
        pal = _mono_palette()
        self._pal = pal

        # digit bitmaps, built once at boot: DIGITS index -> Bitmap
        self._small = [_digit_bitmap(rows, 1) for rows in DIGITS]
        self._big = [_digit_bitmap(rows, 2) for rows in DIGITS]

        # --- layer 0: rain (also the no-link base) ---------------------
        sheet, gw, gh, n = _rain_sheet(terminalio.FONT)
        self._rain_n = n
        cols = km_weather.SCREEN_W // gw
        rows = km_weather.SCREEN_H // gh
        self._rain_grid = displayio.TileGrid(
            sheet, pixel_shader=pal, width=cols, height=rows,
            tile_width=gw, tile_height=gh, default_tile=0)
        # Trail length scales with the grid's real height. terminalio.FONT is
        # 6x12 on CircuitPython 10.x, so `rows` is 5, not the 8 an assumed
        # 6x8 would give -- and the default 3-5 trail lights a drop's entire
        # column there, flattening the head/dim/off gradient into a solid bar
        # (bench 2026-08-23: the ~4px dead strip below the grid is the 6x12
        # tell). Short grids get a 2-3 trail so every drop still leaves dark
        # rows behind it. The threshold is `rows < 7`, not `== 5`, so a
        # future font change lands on the right side of it by itself.
        if rows < 7:
            self._field = km_weather.RainField(random, cols, rows, glyphs=n,
                                               trail_min=2, trail_span=2)
        else:
            self._field = km_weather.RainField(random, cols, rows, glyphs=n)
        self._rain_group = displayio.Group()
        self._rain_group.append(self._rain_grid)

        # A single immutable faceplate plus changed-cell bar/text tiles.
        # Text is our own 5x7 font; terminalio's 6x12 cell won't fit here.
        face = displayio.Bitmap(128, 64, 2)
        for x, y in km_stereo.backdrop():
            face[x, y] = 1
        self._spectrum_group = displayio.Group()
        self._spectrum_group.append(displayio.TileGrid(face, pixel_shader=pal))
        sheet = displayio.Bitmap(28, 4, 2)
        for kind in range(4):
            for x in range(1, 6):
                if kind & 1:
                    sheet[kind * 7 + x, 2] = 1
                    sheet[kind * 7 + x, 3] = 1
                if kind & 2:
                    sheet[kind * 7 + x, 0] = 1
        self._spectrum_grid = displayio.TileGrid(
            sheet, pixel_shader=pal, width=km_spectrum.BANDS,
            height=km_stereo.ROWS, tile_width=7, tile_height=4,
            x=km_stereo.GRID_X, y=km_stereo.GRID_Y)
        self._spectrum_group.append(self._spectrum_grid)
        font_sheet = displayio.Bitmap(len(km_stereo.GLYPHS) * 6, 8, 2)
        for i, ch in enumerate(km_stereo.GLYPHS):
            for y, mask in enumerate(km_stereo.FONT[ch]):
                for x in range(5):
                    if mask & (1 << (4-x)):
                        font_sheet[i * 6 + x, y] = 1
        self._media_grids = []
        for y in (0, 9):
            grid = displayio.TileGrid(font_sheet, pixel_shader=pal,
                                      width=km_stereo.COLS, height=1,
                                      tile_width=6, tile_height=8, x=8, y=y)
            self._media_grids.append(grid)
            self._spectrum_group.append(grid)
        self._readout = km_stereo.Readout(ticks_diff)
        self._media_drawn = (' ' * km_stereo.COLS, ' ' * km_stereo.COLS)
        self._spectrum_group.hidden = True
        self._spectrum = km_spectrum.Spectrum(ticks_diff)
        self._spectrum_clock = km_weather.FrameClock(
            SPECTRUM_FRAME_MS, ticks_ms(), ticks_add, ticks_diff)
        self._spectrum_drawn = [(0, 0)] * km_spectrum.BANDS

        # --- layer 1: bell wall ----------------------------------------
        self._wall_group = displayio.Group()
        self._wall_group.hidden = True
        self._wall_layout = None       # last-drawn layout list

        # --- layer 2: marquee ------------------------------------------
        self._marquee_group = displayio.Group()
        self._marquee_group.hidden = True
        self._marquee_epoch = None
        self._marquee_tile = None

        # --- layer 3: badges (topmost) ---------------------------------
        self._badges = displayio.Group()
        self._rec = label.Label(terminalio.FONT, text=_REC_TEXT,
                                color=0x000000, background_color=0xFFFFFF)
        self._rec.anchor_point = (1.0, 0.0)
        self._rec.anchored_position = (km_weather.SCREEN_W, 0)
        self._rec.hidden = True
        self._submap = label.Label(terminalio.FONT, text=" ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._submap.anchor_point = (1.0, 0.0)
        self._submap.anchored_position = (_SUBMAP_RIGHT, 0)
        self._submap.hidden = True
        self._nolink = label.Label(terminalio.FONT, text=" no link ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._nolink.anchor_point = (1.0, 1.0)
        self._nolink.anchored_position = (km_weather.SCREEN_W,
                                          km_weather.SCREEN_H)
        self._nolink.hidden = True
        self._badges.append(self._rec)
        self._badges.append(self._submap)
        # Bottom-right, so it never collides with the two top-right badges;
        # intra-layer order is therefore immaterial.
        self._badges.append(self._nolink)

        for layer in (self._rain_group, self._spectrum_group, self._wall_group,
                      self._marquee_group, self._badges):
            self.group.append(layer)
        display.root_group = self.group

        # state caches -- write hardware only on change
        self._weather = None
        self._order = None
        self._flags = (None, None)
        self._submap_text = None
        self._clock = km_weather.FrameClock(FRAME_MS, ticks_ms(),
                                            ticks_add, ticks_diff)
        self._rain_acc = 0

    # ---- state ---------------------------------------------------------
    def _sync_layers(self):
        """Derive which base layer shows, from weather + wall contents.

        The caller must already have the panel suspended: this makes up to
        three visibility flips and must not be scanned out half-applied.

        The wall shows only when the state is "ringing" AND it actually has
        numerals. Without that second condition, `set_weather("ringing")`
        arriving before `set_bells` populates the wall would unhide an empty
        (or stale) wall for one frame -- the 489c99a blank-frame class. Rain
        stands in until the wall is real, so no ordering of the two setters
        can ever produce a blank panel.
        """
        show_wall = self._weather == "ringing" and bool(self._wall_layout)
        show_spectrum = km_spectrum.visible(
            self._spectrum.active, self._weather, show_wall)
        _set_hidden(self._rain_group, show_wall or show_spectrum)
        _set_hidden(self._spectrum_group, not show_spectrum)
        _set_hidden(self._wall_group, not show_wall)
        _set_hidden(self._nolink, self._weather != "nolink")

    def set_weather(self, state):
        """Base state: "calm" | "ringing" | "nolink". Idempotent.

        Ordering contract: callers should call `set_bells` before
        `set_weather("ringing")`. This method does not depend on that --
        an empty wall keeps rain showing until `set_bells` fills it -- but
        the intended order avoids a frame of rain before the wall appears.
        """
        if state == self._weather:
            return
        self._weather = state
        self._display.auto_refresh = False
        try:
            self._sync_layers()
        finally:
            self._display.auto_refresh = True

    def set_bells(self, order):
        """Rebuild the bell wall from a bell_order list. Idempotent."""
        # Cheapest guard first. Cockpit calls this once per loop iteration
        # at several hundred hertz and the answer is almost always "no
        # change", so comparing the raw list keeps wall_layout's list-plus-
        # six-tuples allocation off the steady-state path entirely -- that
        # is real GC pressure on an RP2040, not a micro-optimisation.
        if order == self._order:
            return
        self._order = list(order)      # copy: the caller may reuse its list
        layout = km_weather.wall_layout(order)
        if layout == self._wall_layout:
            # Different order, same wall: wall_layout truncates past six.
            return
        self._wall_layout = layout
        # Rare change event: rebuild the layer's children with the panel's
        # auto-refresh suspended, so an async scan-out can never catch the
        # half-built (or empty) wall -- the 489c99a blank-frame bug class.
        self._display.auto_refresh = False
        try:
            while len(self._wall_group):
                self._wall_group.pop()
            for ws, size, x, y in layout:
                bank = self._big if size == "big" else self._small
                tg = displayio.TileGrid(bank[ws % 10],
                                        pixel_shader=self._pal, x=x, y=y)
                self._wall_group.append(tg)
            # Going empty->populated (or back) changes whether the wall may
            # show, so the derivation has to re-run inside the same suspend.
            self._sync_layers()
        finally:
            self._display.auto_refresh = True

    def marquee(self, ws):
        """Show the workspace numeral, centred, for MARQUEE_MS. Event-driven,
        not per-tick: this writes once and tick() only retires it."""
        # Swap the numeral and unhide -- two steps, so the panel is suspended
        # across them. The numeral is placed at its final centred position
        # immediately, which is the entire point: the earlier design slid it
        # across at 13px per frame, and dirtying both the old and new
        # rectangles every frame while the SH1106 scanned out tore visibly
        # (bench 2026-08-23). A held numeral costs one write on show, one on
        # hide, and nothing in between.
        self._display.auto_refresh = False
        try:
            while len(self._marquee_group):
                self._marquee_group.pop()
            tg = displayio.TileGrid(
                self._big[ws % 10], pixel_shader=self._pal,
                x=km_weather.MARQUEE_X, y=km_weather.MARQUEE_Y)
            self._marquee_group.append(tg)
            self._marquee_tile = tg
            _set_hidden(self._marquee_group, False)
        finally:
            self._display.auto_refresh = True
        self._marquee_epoch = ticks_ms()

    def set_flags(self, rec, submap):
        """REC block and [submap] badge. rec is bool, submap a str. Idempotent."""
        if (rec, submap) == self._flags:
            return
        self._flags = (rec, submap)
        # " [name] " at 6px/char, right-anchored at _SUBMAP_RIGHT, which
        # already reserves REC's corner -- so the two badges never contend
        # and the anchor is fixed whether or not REC is showing. The badge
        # never reflows; it is simply dropped when the name will not fit.
        # The fit test and composition live in km_weather.submap_badge so
        # they are host-testable (this file is not).
        # Label.text has no equality short-circuit (docs/pad-timing.md
        # section 5), so the text write goes through its own cache rather
        # than the flags tuple: two different flag tuples can want the same
        # badge text.
        text = km_weather.submap_badge(submap, _SUBMAP_MAX)
        self._display.auto_refresh = False
        try:
            _set_hidden(self._rec, not rec)
            if text != self._submap_text:
                self._submap_text = text
                if text:
                    # Only ever assign non-empty text; an empty Label is
                    # hidden instead, which costs no glyph rebuild.
                    self._submap.text = text
                _set_hidden(self._submap, not text)
        finally:
            self._display.auto_refresh = True

    def set_media(self, msg, now):
        self._readout.receive(msg, now)

    def set_spectrum(self, msg, now):
        # State only. Drawing and expiry are gated by the independent clock.
        self._spectrum.receive(msg, now)

    def _tick_spectrum(self, now):
        if not self._spectrum_clock.advance(now):
            return
        self._spectrum.advance(now)
        show_wall = self._weather == "ringing" and bool(self._wall_layout)
        visible = km_spectrum.visible(self._spectrum.active, self._weather, show_wall)
        # A hidden/static spectrum causes no hardware writes. Prepaint before
        # revealing so resuming music never flashes an old frame.
        changes = []
        if visible:
            for col in range(km_spectrum.BANDS):
                pair = (self._spectrum.bars[col], self._spectrum.peaks[col])
                if pair != self._spectrum_drawn[col]:
                    changes.append((col, pair))
        text = self._readout.frame(now) if visible else self._media_drawn
        # CircuitPython omits str.ljust; slicing/concatenation works on both.
        text = tuple((line + ' ' * km_stereo.COLS)[:km_stereo.COLS] for line in text)
        if not changes and text == self._media_drawn and visible == (not self._spectrum_group.hidden):
            return
        self._display.auto_refresh = False
        try:
            for line, grid in enumerate(self._media_grids):
                for col, ch in enumerate(text[line]):
                    if ch != self._media_drawn[line][col]:
                        index = km_stereo.GLYPHS.find(ch)
                        grid[col, 0] = index if index >= 0 else km_stereo.GLYPHS.index('?')
            self._media_drawn = text
            for col, pair in changes:
                old = self._spectrum_drawn[col]
                for row in range(km_stereo.ROWS):
                    value = km_stereo.tile(pair[0], pair[1], row)
                    if value != km_stereo.tile(old[0], old[1], row):
                        self._spectrum_grid[col, row] = value
                self._spectrum_drawn[col] = pair
            self._sync_layers()
        finally:
            self._display.auto_refresh = True

    # ---- animation ------------------------------------------------------
    def tick(self, now):
        self._tick_spectrum(now)
        # ALL display mutation below is gated behind the frame clock -- the
        # main loop is unthrottled (docs/pad-timing.md section 1), so
        # anything outside this gate would dirty the panel at loop frequency.
        frames = self._clock.advance(now)
        if not frames:
            return

        if self._marquee_epoch is not None:
            # marquee_visible is a pure function of elapsed time, so a slow
            # tick retires the numeral on schedule rather than stretching its
            # stay. The numeral never moves, so this branch touches the panel
            # exactly once per switch -- on the frame that hides it.
            #
            # The clamp is load-bearing. marquee() seeds the epoch from its
            # own ticks_ms() read, while `now` was read by the caller earlier
            # in the same loop iteration -- so if the frame gate fires on the
            # very iteration that started it, elapsed is NEGATIVE.
            # marquee_visible is False for that, which would look identical to
            # "finished" and silently cancel the numeral before it was seen.
            # Clamping keeps this file independent of the caller's clock-read
            # ordering; the cost is that it can retire one frame late, which
            # is 50ms and invisible.
            elapsed = ticks_diff(now, self._marquee_epoch)
            if elapsed < 0:
                elapsed = 0
            if not km_weather.marquee_visible(elapsed):
                self._marquee_epoch = None
                _set_hidden(self._marquee_group, True)

        # Rain runs at a whole-number division of the frame clock. On a
        # catch-up burst the accumulator resets rather than stepping the
        # field N times: dropping skipped rain frames is correct here (the
        # animation is decorative and a burst would read as a glitch),
        # whereas the marquee above must not lose position.
        self._rain_acc += frames
        if self._rain_acc >= RAIN_DIV:
            self._rain_acc = 0
            # A hidden rain layer schedules no work at all, and a calm
            # screen whose field produced no changes writes nothing.
            if not self._rain_group.hidden:
                for col, row, kind, glyph_i in self._field.step():
                    if kind == "off":
                        self._rain_grid[col, row] = 0
                    else:
                        bank = _KIND_BANK[kind] * self._rain_n
                        self._rain_grid[col, row] = 1 + bank + glyph_i
