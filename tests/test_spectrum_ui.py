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


class Palette(dict):
    def __init__(self, n):
        super().__init__()

    def make_transparent(self, n):
        pass


class Label:
    def __init__(self, *args, **kw):
        self.hidden = False


def test_real_renderer_audio_rain_alerts_and_static_writes(monkeypatch):
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
    monkeypatch.setattr(ui, '_rain_sheet', lambda font: (Bitmap(198,12,2),6,12,16))
    display = types.SimpleNamespace(auto_refresh=True)
    screen = ui.Screen(display)
    screen.set_weather('calm')
    screen.set_spectrum({'active': True, 'bars': [8]*16}, 0)
    screen.tick(50)
    assert screen._rain_group.hidden
    assert not screen._spectrum_group.hidden
    grid = screen._spectrum_grid
    assert grid.values[0,4] == 3   # top segment and cap
    screen.set_media({'title':'Summer lofi radio','artist':'Lofi Girl'},50)
    screen.tick(100)
    assert screen._media_drawn == ('SUMMER LOFI RADIO   '.ljust(20), 'LOFI GIRL'.ljust(20))
    text_writes = sum(g.writes for g in screen._media_grids)
    writes = grid.writes
    screen.tick(150)
    assert sum(g.writes for g in screen._media_grids) == text_writes
    assert grid.writes == writes  # no mutation of an identical frame
    screen.set_bells([3])
    screen.set_weather('ringing')
    assert not screen._wall_group.hidden and screen._spectrum_group.hidden
    screen.set_spectrum({'active': True, 'bars': [12]*16}, 110)
    screen.tick(150)
    assert grid.writes == writes  # hidden spectrum does no drawing
    assert sum(g.writes for g in screen._media_grids) == text_writes
    screen.set_bells([])
    screen.set_weather('calm')
    screen.tick(200)
    assert not screen._spectrum_group.hidden and grid.writes > writes
    screen.marquee(4)
    assert not screen._marquee_group.hidden
    assert list(screen.group).index(screen._marquee_group) > list(screen.group).index(screen._spectrum_group)
    screen.tick(1650)
    assert screen._spectrum_group.hidden and not screen._rain_group.hidden
    screen.set_spectrum({'active': True, 'bars': [4]*16}, 1700)
    screen.set_weather('nolink')
    screen.tick(1750)
    assert screen._spectrum_group.hidden and not screen._rain_group.hidden
    assert display.auto_refresh
