from km_screen import SCREEN_W, SCREEN_H, submap_badge, FrameClock


def test_submap_badge_composes_with_brackets_and_padding():
    assert submap_badge("resize", 11) == " [resize] "


def test_submap_badge_boundary_is_exact():
    # 11 fits, 12 does not -- the pad's _SUBMAP_MAX is 11 characters.
    assert submap_badge("a" * 11, 11) == " [aaaaaaaaaaa] "
    assert submap_badge("a" * 12, 11) == ""


def test_submap_badge_empty_name_is_dropped():
    assert submap_badge("", 11) == ""


def test_frameclock_not_due_returns_zero():
    clock = FrameClock(100, now=1000, add=lambda a, b: a + b,
                       diff=lambda a, b: a - b)
    assert clock.advance(1050) == 0
    assert clock.advance(1099) == 0


def test_frameclock_counts_skipped_frames_and_does_not_drift():
    add, diff = (lambda a, b: a + b), (lambda a, b: a - b)
    clock = FrameClock(100, now=1000, add=add, diff=diff)
    assert clock.advance(1100) == 1
    assert clock.advance(1550) == 4       # a stall: frames counted, not lost
    # deadlines stay on the epoch grid: next due at exactly 1600
    assert clock.advance(1599) == 0
    assert clock.advance(1600) == 1


def test_frameclock_wraparound_safe():
    period = 2 ** 29

    def wdiff(a, b):
        return ((a - b + period // 2) % period) - period // 2

    def wadd(a, b):
        return (a + b) % period

    clock = FrameClock(100, now=period - 50, add=wadd, diff=wdiff)
    assert clock.advance(period - 10) == 0
    assert clock.advance(60) == 1         # wrapped past the deadline at 50
