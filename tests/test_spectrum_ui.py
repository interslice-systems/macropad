"""Exercise real renderer code with inert displayio objects, not hardware timing."""
import importlib.util
import sys
import types
from pathlib import Path


class Group(list):
    hidden = False


class Bitmap:
    def __init__(self, width, height, colors):
        self.width, self.height = width, height
        self.values = {}

    def __setitem__(self, key, value):
        self.values[key] = value

    def __getitem__(self, key):
        return self.values.get(key, 0)


class TileGrid:
    def __init__(self, bitmap, **kw):
        self.bitmap, self.kw = bitmap, kw
        self.values = {}
        self.writes = 0
        self.hidden = False

    def __setitem__(self, key, value):
        self.values[key] = value
        self.writes += 1

    def __getitem__(self, key):
        return self.values.get(key, self.kw.get('default_tile', 0))


class Palette(dict):
    def __init__(self, n):
        super().__init__()

    def make_transparent(self, n):
        pass


class Label:
    def __init__(self, *args, **kw):
        self.hidden = False


def test_real_renderer_faceplate_is_the_whole_display(monkeypatch):
    monkeypatch.setitem(sys.modules, 'displayio', types.SimpleNamespace(
        Group=Group, Bitmap=Bitmap, TileGrid=TileGrid, Palette=Palette))
    monkeypatch.setitem(sys.modules, 'terminalio', types.SimpleNamespace(FONT=None))
    monkeypatch.setitem(sys.modules, 'adafruit_display_text', types.SimpleNamespace(
        label=types.SimpleNamespace(Label=Label)))
    monkeypatch.setitem(sys.modules, 'adafruit_ticks', types.SimpleNamespace(
        ticks_ms=lambda: 0, ticks_add=lambda a,b: a+b, ticks_diff=lambda a,b: a-b))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'firmware'))
    spec = importlib.util.spec_from_file_location('spectrum_ui_test',
        Path(__file__).parents[1] / 'firmware/pad/ui.py')
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    # CircuitPython's dict order differs: the actual pad's tile zero was '/'.
    glyphs = ui.km_stereo.GLYPHS
    monkeypatch.setattr(ui.km_stereo, 'GLYPHS', '/' + glyphs.replace('/', ''))
    display = types.SimpleNamespace(auto_refresh=True)
    screen = ui.Screen(display)
    assert not hasattr(screen, '_rain_group') and not hasattr(screen, '_wall_group')
    assert not screen._spectrum_group.hidden           # always the base layer
    for text_grid in screen._media_grids:
        assert all(ui.km_stereo.GLYPHS[text_grid[col, 0]] == ' ' for col in range(20))
    screen.set_link(False)
    assert not screen._nolink.hidden and not screen._spectrum_group.hidden
    screen.set_link(True)
    assert screen._nolink.hidden
    screen.tick(50)
    assert screen._media_drawn == ('SYSTEM AUDIO'.ljust(20), 'OPERATOR'.ljust(20))
    screen.set_spectrum({'active': True, 'bars': [8]*16}, 60)
    screen.tick(100)
    grid = screen._spectrum_grid
    assert grid.values[0,4] == 3   # top segment and cap
    screen.set_media({'title':'Summer lofi radio','artist':'Lofi Girl'},100)
    screen.tick(150)
    assert screen._media_drawn == ('SUMMER LOFI RADIO'.ljust(20), 'LOFI GIRL'.ljust(20))
    for line, text_grid in zip(screen._media_drawn, screen._media_grids):
        assert ''.join(ui.km_stereo.GLYPHS[text_grid[col, 0]] for col in range(20)) == line
    text_writes = sum(g.writes for g in screen._media_grids)
    writes = grid.writes
    screen.tick(200)
    assert sum(g.writes for g in screen._media_grids) == text_writes
    assert grid.writes == writes  # no mutation of an identical frame
    screen.set_tune({'title': 'jazz lofi radio', 'line': 'TUNING', 'hold': 3}, 210)
    screen.tick(250)
    assert screen._media_drawn == ('JAZZ LOFI RADIO'.ljust(20), 'TUNING'.ljust(20))
    screen.set_spectrum({'active': False, 'bars': [0]*16}, 260)
    screen.tick(300)
    assert grid.values[0,4] == 0 and not screen._spectrum_group.hidden   # silent, still framed
    screen.set_flags(True, 'resize')
    assert not screen._rec.hidden and not screen._submap.hidden
    assert display.auto_refresh
