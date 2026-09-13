"""Spectrum state and peak holds; shared by CircuitPython and host tests."""

BANDS = 16
LEVELS = 16
HOLD_MS = 1000
FALL_MS = 80
STALE_MS = 1500


class Spectrum:
    def __init__(self, diff):
        self.diff = diff
        self.bars = [0] * BANDS
        self.peaks = [0] * BANDS
        self._raised = [0] * BANDS
        self._tops = [0] * BANDS
        self.received = None
        self.active = False

    def receive(self, msg, now):
        bars = msg.get("bars")
        if (type(msg.get("active")) is not bool or not isinstance(bars, list)
                or len(bars) != BANDS
                or any(type(v) is not int or not 0 <= v <= LEVELS for v in bars)):
            return False
        was_active = self.active
        self.active = msg["active"]
        self.received = now
        if not self.active or not was_active:
            self.peaks = [0] * BANDS
            self._tops = [0] * BANDS
        self.bars = list(bars) if self.active else [0] * BANDS
        self.advance(now)
        return True

    def advance(self, now):
        if self.received is None or self.diff(now, self.received) >= STALE_MS:
            self.active = False
        if not self.active:
            return
        for i, bar in enumerate(self.bars):
            elapsed = max(0, self.diff(now, self._raised[i]))
            fallen = max(0, (elapsed - HOLD_MS) // FALL_MS)
            peak = max(0, self._tops[i] - fallen)
            if bar and bar >= peak:
                self._tops[i] = peak = bar
                self._raised[i] = now
            self.peaks[i] = peak


def tile(bar, peak, row):
    """8x4 cell: bit 0 = segment, bit 1 = peak cap; rows run top-down."""
    level = LEVELS - row
    return int(level <= bar) | (2 if peak == level else 0)


def visible(active, weather, wall, tuning=False):
    return (active or tuning) and weather != "nolink" and not wall
