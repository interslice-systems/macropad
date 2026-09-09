"""Color math for the pad. Pure: no CircuitPython imports."""

DEFAULT = {
    "name": "default",
    "accent": "88CCFF", "bg": "111111", "fg": "DDDDDD",
    "red": "FF5555", "muted": "446677",
}

# Occupied keys are the DISPLAY, not an indicator. Under the colorhash blocks
# design it is colour that tells sessions apart, so the non-active keys carry
# the primary information and need enough light to render it.
#
# Two facts set this number, both measured on the pad 2026-08-21 (led-ramp):
#
#  1. scale() multiplies a GAMMA-ENCODED byte, so a factor f changes emitted
#     light by roughly f**2.2 -- not f. At 0.25 an occupied key emitted ~4.7%
#     of the active key's light, and after BRIGHTNESS the whole palette
#     collapsed into PWM 4..17: a 13-step window in which lightness could not
#     function as a distinguishing channel at all.
#  2. Chris's comfort floor -- the dimmest a lighted block should be, judged
#     at a glance from his seat in his room, NOT a detection threshold -- is
#     PWM 4 of 255. Detection runs at or below PWM 1; 4 is the design number.
#
# 0.55 widens the window to PWM 9..39 and drops the usable lightness floor from
# OKLab L 0.341 to 0.218. Raising it further keeps helping (0.70 -> 11..49) at
# the cost of contrast against the active key.
OCCUPIED_SCALE = 0.55

# A bell key blinks like a car's hazard lamp: half the cycle lit, half dark,
# hard edges, no fade. Chosen on hardware 2026-08-23 (firmware/apps/pulselab.py,
# since deleted) from four candidate waveforms, then from a 2x2 of floor and
# period. Chris's words: "if you have any sense of what a hazard flasher on a
# car looks like, that's the model."
#
# This RETIRES the old rule that an alert must never sit dimmer than an
# occupied key, which is why the floor is 0.0 and not OCCUPIED_SCALE. That rule
# was right for the waveform it was written for -- a smooth 0.55 -> 1.0 ramp,
# where dipping under ambient makes the key briefly ambiguous with its
# neighbours and the alert just looks like a dim workspace. A hard blink
# signals through CHANGE, not through absolute brightness: nothing else on the
# deck switches, so the dark half reads as alarm rather than as recession. The
# old rule survives in the tests, inverted, so this reversal stays visible.
#
# The known cost, considered and accepted on hardware: during the dark half a
# ringing key is the same black as an EMPTY key. A faint ember floor (0.12) was
# built and eyeballed side by side specifically to avoid that collision, and
# rejected -- the blink alone disambiguates, and the ember cost the hazard-lamp
# look that is the entire point.
URGENT_DARK = 0.0        # the off phase is genuinely off
URGENT_DUTY = 0.5        # 50/50, as an automotive flasher runs


def urgent_factor(phase):
    """Blink factor for a bell/urgent key. `phase` is a LINEAR 0..1 ramp over
    the blink period -- not the triangle wave this used to take, since a
    square wave needs to know where it is in the cycle, not how far from the
    ends. Lit for the first half, dark for the second."""
    return 1.0 if phase < URGENT_DUTY else URGENT_DARK


def hex_to_int(s):
    s = s.lstrip("#")
    return int(s[:6], 16)


def scale(rgb, f):
    r = int(((rgb >> 16) & 0xFF) * f)
    g = int(((rgb >> 8) & 0xFF) * f)
    b = int((rgb & 0xFF) * f)
    return (r << 16) | (g << 8) | b


def _c(pal, key):
    return hex_to_int(pal.get(key) or DEFAULT[key])


def key_color(state, pal, phase=0.0):
    if state == "active":
        return _c(pal, "accent")
    if state == "occupied":
        return scale(_c(pal, "accent"), OCCUPIED_SCALE)
    if state == "urgent":
        return scale(_c(pal, "red"), urgent_factor(phase))
    return 0


def ws_key_color(state, ws_hex, pal, phase=0.0):
    """Top-half key: the workspace's colorhash hue for active/occupied.

    Urgent and empty ignore the hue on purpose -- urgency is an alert, not an
    identity, and must look the same on every key. No hue at all (unnamed
    workspace, missing palette.json) falls back to the theme accent.
    """
    if ws_hex is None or state in ("urgent", "empty"):
        return key_color(state, pal, phase)
    c = hex_to_int(ws_hex)
    if state == "active":
        return c
    if state == "occupied":
        return scale(c, OCCUPIED_SCALE)
    return 0


def ctx_key_color(item, pal, phase=0.0):
    """Bottom-half key for one ctx item ({"c", "active", "bell"}); None -> off.

    Fail closed on colour, never on the alert: an item without an identity hue
    renders dark, unless it is ringing -- a lost bell is worse than a lost hue.
    """
    if item is None:
        return 0
    if item.get("bell"):
        return scale(_c(pal, "red"), urgent_factor(phase))
    if not item.get("c"):
        return 0
    c = hex_to_int(item["c"])
    return c if item.get("active") else scale(c, OCCUPIED_SCALE)


# ---- deck key states (spec section 5.3) ------------------------------------
# Hue carries workspace identity and NOTHING else. States differ by brightness
# and animation only -- which is also the only encoding that survives Chris's
# CVD and a swappable theme.
#
# 0.34 sits between two measured points documented above: 0.25, which collapsed
# the whole palette into a 13-step PWM window where lightness stopped working as a
# channel at all, and OCCUPIED_SCALE 0.55. A ghost must read as recessive against
# a live key while still carrying its hue -- which is the one thing it still has
# to say: whose workspace it belonged to.
#
# NOT derived from the PWM-4 comfort floor. That floor is a property of the byte
# AFTER neopixel's global BRIGHTNESS is applied, which scale() does not include,
# so no unit test here can assert it. Validate the emitted floor on hardware with
# daemon/operatord/ledtest.py and led-ramp before treating 0.34 as final.
GHOST_SCALE = 0.34


def state_factor(state, phase=0.0):
    """Brightness factor for a deck key state. Pass phase for 'bell'."""
    if state == "focused":
        return 1.0
    if state == "bell":
        return urgent_factor(phase)
    if state == "live":
        return OCCUPIED_SCALE
    if state == "ghost":
        return GHOST_SCALE
    return 0.0


def deck_key_color(state, ws_hex, phase=0.0):
    """Final pixel value for a deck key. Colour decisions live here so the
    firmware only assigns, and so they are testable -- CircuitPython does
    not run pytest."""
    if state == "empty" or not ws_hex:
        return 0x000000
    return scale(hex_to_int(ws_hex), state_factor(state, phase))
