"""Omarchy theme → palette messages. Contract: colors.toml keys + theme path pair."""
import asyncio
import re
import tomllib
from pathlib import Path

_HEX = re.compile(r"^[0-9a-fA-F]{6}$")

# Semantic keys with 3.8.4 ANSI fallbacks (observed on nexus: no red/muted keys).
_FALLBACKS = {
    "accent": ("accent",),
    "bg": ("background",),
    "fg": ("foreground",),
    "red": ("red", "color1"),
    "muted": ("muted", "color8"),
}


def resolve_theme_dir(home):
    home = Path(home)
    for p in (home / ".local/state/omarchy/current/theme",     # Omarchy 4.x
              home / ".config/omarchy/current/theme"):         # Omarchy 3.x
        if p.exists():
            return p.resolve()
    return None


def load_palette(theme_dir):
    f = Path(theme_dir) / "colors.toml"
    try:
        data = tomllib.loads(f.read_text())
    except (OSError, ValueError):
        return None
    pal = {"t": "palette", "name": Path(theme_dir).name}
    for dst, keys in _FALLBACKS.items():
        for k in keys:
            v = data.get(k)
            if isinstance(v, str):
                h = v.lstrip("#")
                if _HEX.match(h):
                    pal[dst] = h.lower()
                    break
    return pal if "accent" in pal else None


class ThemeWatcher:
    def __init__(self, home, on_palette, poll_s=2.0):
        self.home = home
        self.on_palette = on_palette
        self.poll_s = poll_s
        self._seen = None

    def check(self):
        """One poll step; returns the palette if it changed. Sync for testability."""
        d = resolve_theme_dir(self.home)
        if d is None:
            return None
        try:
            key = (str(d), (d / "colors.toml").stat().st_mtime_ns)
        except OSError:
            return None
        if key == self._seen:
            return None
        pal = load_palette(d)
        if pal is not None:
            self._seen = key
        return pal

    async def run(self):
        while True:
            pal = self.check()
            if pal is not None:
                await self.on_palette(pal)
            await asyncio.sleep(self.poll_s)
