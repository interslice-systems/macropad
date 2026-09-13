"""OLED helpers that survived the weather display: panel geometry, the
submap badge, and the frame clock. Pure; runs on CPython and CircuitPython.

The rain, bell wall and workspace marquee were retired 2026-09-13 -- the
display is the radio's faceplate now (see km_stereo). Their history lives
in docs/specs/2026-08-23-oled-weather-design.md.
"""

SCREEN_W = 128
SCREEN_H = 64


def submap_badge(submap, max_len):
    """Compose the " [name] " submap badge, or "" when it must be dropped.

    Lives here rather than at the displayio edge because it is pure string
    logic and the drawing edge is not host-testable. The badge is
    right-anchored at a FIXED position that already reserves REC's corner,
    so the two can never contend: the only reason to drop it is a name too
    long to fit in the space left of that anchor. `max_len` is that budget
    in characters, computed from the panel geometry by the caller.
    """
    if not submap or len(submap) > max_len:
        return ""
    return " [" + submap + "] "


class FrameClock:
    """Fixed-rate animation scheduler, epoch-anchored and wraparound-safe.

    Deadlines sit on the grid epoch + n*period: each deadline is advanced by
    an exact add(prev, period), which is the same grid with no accumulated
    drift (docs/pad-timing.md section 3 forbids chaining from *event
    occurrence* times, not constant-period deadline advancement). advance()
    returns how many whole periods elapsed, so a caller can account for
    frames a slow tick skipped instead of silently losing them.
    """

    def __init__(self, period, now, add, diff):
        self.period = period
        self.add = add
        self.diff = diff
        self.next_at = add(now, period)

    def advance(self, now):
        n = 0
        while self.diff(now, self.next_at) >= 0:
            self.next_at = self.add(self.next_at, self.period)
            n += 1
        return n
