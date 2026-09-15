import km_palette as kp


def test_hex_to_int_with_and_without_hash():
    assert kp.hex_to_int("#faa968") == 0xFAA968
    assert kp.hex_to_int("FAA968") == 0xFAA968


def test_scale_halves_channels():
    assert kp.scale(0x804020, 0.5) == 0x402010
    assert kp.scale(0xFFFFFF, 0.0) == 0x000000


def test_key_color_states():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.key_color("active", pal) == 0xFF0000
    assert kp.key_color("occupied", pal) == kp.scale(0xFF0000, kp.OCCUPIED_SCALE)
    assert kp.key_color("empty", pal) == 0
    lit = kp.key_color("urgent", pal, phase=0.25)
    dark = kp.key_color("urgent", pal, phase=0.75)
    assert lit == 0xFF0000 and dark == 0       # blinks the accent it wears


def test_key_color_missing_keys_fall_back_to_default():
    assert kp.key_color("active", {}) == kp.hex_to_int(kp.DEFAULT["accent"])


# --- the pad's usable light budget: measured on hardware, not derived ---
#
# BRIGHTNESS lives in firmware/pad/framework.py and is applied by neopixel to
# whatever byte we store, so the light a key emits is int(byte * OCCUPIED_SCALE)
# * BRIGHTNESS. These two tests pin the properties that were actually broken,
# rather than pinning OCCUPIED_SCALE itself (which the other tests reference
# symbolically and would therefore follow silently in any direction).
BRIGHTNESS = 0.3
FLOOR_PWM = 4          # Chris's comfort floor, measured 2026-08-21 via led-ramp
BYTE_AT_L_FLOOR = 56   # sRGB byte of OKLab L 0.341, the floor's lightness
BYTE_AT_L_TOP = 239    # sRGB byte of OKLab L 0.95, top of the LED band


def _emitted(byte):
    return int(int(byte * kp.OCCUPIED_SCALE) * BRIGHTNESS)


def test_occupied_keys_clear_the_measured_comfort_floor():
    assert _emitted(BYTE_AT_L_FLOOR) >= FLOOR_PWM


def test_occupied_keys_keep_enough_range_for_lightness_to_mean_anything():
    """At OCCUPIED_SCALE 0.25 the whole palette collapsed into PWM 4..17.

    Thirteen steps is not enough range for lightness to distinguish anything on
    the pad, which defeats the colorhash lightness floor on the one surface it
    was most wanted. Guard the span, not the constant.
    """
    span = _emitted(BYTE_AT_L_TOP) - _emitted(BYTE_AT_L_FLOOR)
    assert span >= 25, f"occupied span collapsed to {span} PWM steps"


def test_urgent_blinks_fully_dark_reversing_the_old_never_dimmer_rule():
    """This test used to assert the OPPOSITE, and the reversal is deliberate.

    The old rule -- an alert must never sit dimmer than an occupied key --
    was written for a smooth 0.55 -> 1.0 ramp, where dipping under ambient
    makes a bell look like a dim workspace. The shipped waveform is a hazard
    lamp: dark off phase, signalling through change rather than brightness.
    Chosen on hardware 2026-08-23. If someone reinstates a floor, this test
    should fail loudly rather than let the blink quietly soften.
    """
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.urgent_factor(0.25) == 1.0
    assert kp.urgent_factor(0.75) == 0.0
    assert kp.key_color("urgent", pal, phase=0.75) == 0
    # ...and it is genuinely darker than an occupied key, on purpose.
    assert kp.urgent_factor(0.75) < kp.OCCUPIED_SCALE


def test_urgent_blink_has_even_duty_with_softened_edges_not_a_breathing_wave():
    """Softened 2026-09-15: short eased edges, still mostly a square wave.

    The plateaus must dominate -- a breathing wave (all transition) was
    explicitly not wanted -- and each edge is centred on the old hard edge, so
    the lit/dark balance chosen on hardware is unchanged.
    """
    samples = [kp.urgent_factor(i / 1000) for i in range(1000)]
    assert abs(sum(samples) - 500) < 1.0
    plateau = sum(1 for v in samples if v in (0.0, 1.0))
    assert plateau >= 1000 * (1 - 2 * kp.URGENT_EDGE) - 2
    assert kp.urgent_factor(0.0) == 0.5 and abs(kp.urgent_factor(0.5) - 0.5) < 1e-9
    # Monotone through each edge: eased, never flickering.
    rise = [kp.urgent_factor(p / 1000 % 1.0) for p in range(940, 1061)]
    fall = [kp.urgent_factor(p / 1000) for p in range(440, 561)]
    assert rise == sorted(rise) and fall == sorted(fall, reverse=True)


def test_state_factor_orders_states_by_brightness():
    assert kp.state_factor("focused") == 1.0
    assert kp.state_factor("live") == kp.OCCUPIED_SCALE
    assert kp.state_factor("ghost") < kp.OCCUPIED_SCALE
    assert kp.state_factor("empty") == 0.0


def test_a_deck_bell_blinks_the_same_hazard_waveform_as_a_workspace_bell():
    # Both halves of the deck must alert identically -- a bell is a bell.
    # This too used to assert a floor at OCCUPIED_SCALE; see
    # test_urgent_blinks_fully_dark_reversing_the_old_never_dimmer_rule.
    assert kp.state_factor("bell", phase=0.25) == 1.0
    assert kp.state_factor("bell", phase=0.75) == 0.0
    for i in range(0, 100):
        p = i / 100
        assert kp.state_factor("bell", phase=p) == kp.urgent_factor(p)


def test_a_ghost_keeps_its_hue_on_the_darkest_cell_in_the_palette():
    # The one thing a ghost must still communicate is WHICH workspace it was.
    # #6d0a9e is Petroff 10's darkest led cell (channels 6d/0a/9e); its green
    # channel is the first thing to quantise away as the factor drops.
    rgb = kp.hex_to_int("6d0a9e")
    dim = kp.scale(rgb, kp.state_factor("ghost"))
    r, g, b = (dim >> 16) & 0xFF, (dim >> 8) & 0xFF, dim & 0xFF
    assert g > 0, "green quantised to zero: the hue is gone, not dimmed"
    assert b > r > g, "channel ordering must survive dimming"
    full_ratio = 0x6d / 0x9e
    assert abs(r / b - full_ratio) < 0.02, "hue drifted while dimming"


def test_white_neutral_survives_dimming_as_white():
    dim = kp.scale(kp.hex_to_int("ffffff"),
                   kp.state_factor("ghost"))
    r, g, b = (dim >> 16) & 0xFF, (dim >> 8) & 0xFF, dim & 0xFF
    assert r == g == b                          # still achromatic when dim


def test_deck_key_color_mirrors_the_existing_key_colour_helpers():
    # key_color and state_factor already own colour decisions so the
    # firmware only assigns. deck_key_color builds on that family; without
    # it the scale maths would sit in cockpit.py where nothing can test it.
    lit = kp.deck_key_color("focused", "e16000", 0.0)
    assert lit == kp.hex_to_int("e16000")
    assert kp.deck_key_color("empty", "e16000", 0.0) == 0x000000
    live = kp.deck_key_color("live", "e16000", 0.0)
    ghost = kp.deck_key_color("ghost", "e16000", 0.0)
    assert ((lit >> 16) & 0xFF) > ((live >> 16) & 0xFF) > ((ghost >> 16) & 0xFF)


def test_deck_key_color_blinks_a_bell_between_focused_and_dark():
    # phase 0.25 is mid LIT half and 0.75 mid dark half (0.0 and 0.5 are the
    # centres of the eased edges). Under the old fade, phase=1.0 was peak
    # brightness; under the blink it is the middle of the rise.
    lit = kp.deck_key_color("bell", "e16000", 0.25)
    dark = kp.deck_key_color("bell", "e16000", 0.75)
    assert lit == kp.deck_key_color("focused", "e16000", 0.0)
    assert dark == 0x000000


# ---- split deck: top-half workspace keys, bottom-half window keys ----------

def test_ws_key_color_uses_the_workspace_hue_for_active_and_occupied():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", "e16000", pal) == kp.hex_to_int("e16000")
    assert kp.ws_key_color("occupied", "e16000", pal) == \
        kp.scale(kp.hex_to_int("e16000"), kp.OCCUPIED_SCALE)


def test_ws_key_color_urgent_blinks_the_workspace_hue_and_empty_ignores_it():
    # Reversed 2026-09-15: a ringing key keeps its identity. The theme red it
    # used to swap to was a pastel that rendered near-white on the LEDs.
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("urgent", "e16000", pal, 0.25) == kp.hex_to_int("e16000")
    assert kp.ws_key_color("urgent", "e16000", pal, 0.75) == 0
    assert kp.ws_key_color("urgent", None, pal, 0.25) == 0xFF0000   # accent fallback
    assert kp.ws_key_color("empty", "e16000", pal) == 0


def test_ws_key_color_without_a_hue_falls_back_to_theme_accent():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", None, pal) == kp.key_color("active", pal)


def test_ctx_key_color_empty_slot_is_off():
    assert kp.ctx_key_color(None, {}) == 0


def test_ctx_key_color_active_full_others_dimmed():
    pal = {"red": "00FF00"}
    on = kp.ctx_key_color({"c": "e16000", "active": True, "bell": False}, pal)
    off = kp.ctx_key_color({"c": "e16000", "active": False, "bell": False}, pal)
    assert on == kp.hex_to_int("e16000")
    assert off == kp.scale(kp.hex_to_int("e16000"), kp.OCCUPIED_SCALE)


def test_ctx_key_color_bell_blinks_its_identity_hue():
    pal = {"red": "00FF00"}
    lit = kp.ctx_key_color({"c": "e16000", "active": False, "bell": True}, pal, 0.25)
    dark = kp.ctx_key_color({"c": "e16000", "active": False, "bell": True}, pal, 0.75)
    assert lit == kp.hex_to_int("e16000")
    assert dark == 0x000000


def test_ctx_key_color_missing_hue_stays_dark_but_a_bell_still_alerts():
    # Fail closed on colour, never on the alert: an item that arrives without
    # an identity hue renders off, unless it is ringing.
    pal = {"red": "00FF00"}
    assert kp.ctx_key_color({"c": None, "active": True, "bell": False}, pal) == 0
    assert kp.ctx_key_color({"c": None, "active": False, "bell": True}, pal, 0.25) \
        == 0x00FF00
