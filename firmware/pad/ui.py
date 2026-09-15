"""OLED: the radio faceplate, with badges on top.

Layer order, bottom to top (see Screen.__init__):

  0  faceplate  the stereo bezel, 16 spectrum bars, and the two-line readout
                (km_stereo); always showing, silent or not
  1  badges     REC + [submap] + the "no link" tag; topmost, composited last

Discipline per
docs/pad-timing.md section 5 still holds: every write path diffs against what
was last written; a static layer costs zero work; no live bitmap is ever
cleared wholesale.

Every layer's palette makes color 0 transparent, so the badges composite over
the faceplate rather than occlude it.

This module is not host-testable on real displayio (firmware-only), but
tests/test_spectrum_ui.py drives it with inert stand-ins.
"""
import displayio
import terminalio
from adafruit_display_text import label
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

import km_screen
import km_spectrum
import km_stereo

SPECTRUM_FRAME_MS = 50       # 20 fps, matches the daemon's analyzer rate

# badges: terminalio.FONT is 6px wide per cell; badges are Labels
_BADGE_CELL = 6
_REC_TEXT = " REC "
_SUBMAP_RIGHT = km_screen.SCREEN_W - len(_REC_TEXT) * _BADGE_CELL - 4
# " [name] " must fit left of REC's reserved corner
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


class Screen:
    def __init__(self, display):
        self._display = display
        self.group = displayio.Group()
        pal = _mono_palette()
        self._pal = pal

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
                                      tile_width=6, tile_height=8, x=8, y=y,
                                      default_tile=km_stereo.GLYPHS.index(' '))
            self._media_grids.append(grid)
            self._spectrum_group.append(grid)
        self._readout = km_stereo.Readout(ticks_diff)
        self._media_drawn = (' ' * km_stereo.COLS, ' ' * km_stereo.COLS)
        self._spectrum = km_spectrum.Spectrum(ticks_diff)
        self._spectrum_clock = km_screen.FrameClock(
            SPECTRUM_FRAME_MS, ticks_ms(), ticks_add, ticks_diff)
        self._spectrum_drawn = [(0, 0)] * km_spectrum.BANDS

        # --- layer 3: badges (topmost) ---------------------------------
        self._badges = displayio.Group()
        self._rec = label.Label(terminalio.FONT, text=_REC_TEXT,
                                color=0x000000, background_color=0xFFFFFF)
        self._rec.anchor_point = (1.0, 0.0)
        self._rec.anchored_position = (km_screen.SCREEN_W, 0)
        self._rec.hidden = True
        self._submap = label.Label(terminalio.FONT, text=" ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._submap.anchor_point = (1.0, 0.0)
        self._submap.anchored_position = (_SUBMAP_RIGHT, 0)
        self._submap.hidden = True
        self._nolink = label.Label(terminalio.FONT, text=" no link ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._nolink.anchor_point = (1.0, 1.0)
        self._nolink.anchored_position = (km_screen.SCREEN_W,
                                          km_screen.SCREEN_H)
        self._nolink.hidden = True
        self._badges.append(self._rec)
        self._badges.append(self._submap)
        # Bottom-right, so it never collides with the two top-right badges;
        # intra-layer order is therefore immaterial.
        self._badges.append(self._nolink)

        for layer in (self._spectrum_group, self._badges):
            self.group.append(layer)
        display.root_group = self.group

        # state caches -- write hardware only on change
        self._link = None
        self._flags = (None, None)
        self._submap_text = None

    # ---- state ---------------------------------------------------------
    def set_link(self, up):
        """The "no link" tag, over the faceplate. Idempotent."""
        if up == self._link:
            return
        self._link = up
        _set_hidden(self._nolink, up)

    def set_flags(self, rec, submap):
        """REC block and [submap] badge. rec is bool, submap a str. Idempotent."""
        if (rec, submap) == self._flags:
            return
        self._flags = (rec, submap)
        # " [name] " at 6px/char, right-anchored at _SUBMAP_RIGHT, which
        # already reserves REC's corner -- so the two badges never contend
        # and the anchor is fixed whether or not REC is showing. The badge
        # never reflows; it is simply dropped when the name will not fit.
        # The fit test and composition live in km_screen.submap_badge so
        # they are host-testable (this file is not).
        # Label.text has no equality short-circuit (docs/pad-timing.md
        # section 5), so the text write goes through its own cache rather
        # than the flags tuple: two different flag tuples can want the same
        # badge text.
        text = km_screen.submap_badge(submap, _SUBMAP_MAX)
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

    def set_tune(self, msg, now):
        self._readout.tune(msg, now)

    def set_spectrum(self, msg, now):
        # State only. Drawing and expiry are gated by the independent clock.
        self._spectrum.receive(msg, now)

    def _tick_spectrum(self, now):
        if not self._spectrum_clock.advance(now):
            return
        self._spectrum.advance(now)
        changes = []
        for col in range(km_spectrum.BANDS):
            pair = (self._spectrum.bars[col], self._spectrum.peaks[col])
            if pair != self._spectrum_drawn[col]:
                changes.append((col, pair))
        text = self._readout.frame(now)
        # CircuitPython omits str.ljust; slicing/concatenation works on both.
        text = tuple((line + ' ' * km_stereo.COLS)[:km_stereo.COLS] for line in text)
        if not changes and text == self._media_drawn:
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
        finally:
            self._display.auto_refresh = True

    # ---- animation ------------------------------------------------------
    def tick(self, now):
        # ALL display mutation is gated behind the spectrum frame clock -- the
        # main loop is unthrottled (docs/pad-timing.md section 1), so anything
        # outside that gate would dirty the panel at loop frequency.
        self._tick_spectrum(now)
