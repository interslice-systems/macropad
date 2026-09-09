"""Debug LED bridge: watch a spool file, push a ledtest frame to the pad.

Tooling for palette eyeballing (colorhash). The daemon converts hex to the byte
triples the pad stores, so the pad stays dumb -- this deliberately supersedes
the spec's `colors`-hex wire sketch, keeping all parsing on the host side.

NO GAMMA DECODE, and that is deliberate. This bridge exists so a palette can be
eyeballed as the pad will really show it, and the app path (km_palette ->
pixels[n]) writes the encoded byte straight to the pixel with no decode of any
kind. A decode here made the SAME hex render three different ways -- OKLab L
0.95 gave PWM 56 through this bridge, 67 as an active Cockpit key, and 16 as an
occupied one -- so the eyeball test validated a rendering the app never
produces. Worse, decoding is not a fix waiting to happen: it maps dark palette
bytes to near-zero linear values, and at the pad's BRIGHTNESS a cell at the
measured comfort floor (OKLab L 0.341) decodes to PWM 0. Literally off.

Treating an encoded byte as a linear duty cycle is wrong physics and it
desaturates: emitted channel ratios follow the encoded values rather than the
intended light. That is a real fidelity cost, tracked as a separate question
against BRIGHTNESS -- but it is the app's behaviour, so it is this bridge's
behaviour too. One renderer, one appearance.

send is SerialLink.send: SYNCHRONOUS, returns bool -- never await it.
Every ValueError is caught here: this coroutine shares Supervisor.run()'s
gather, so an escaping exception kills the whole daemon.
"""
import asyncio
import json
import os

N_KEYS = 12
HOLD_MIN, HOLD_MAX = 1, 300

# int(x, 16) is NOT a hex-digit check: it accepts signs and surrounding
# whitespace. int("-1", 16) == -1, which used to reach a fractional power and
# raise TypeError out of round(complex), killing the daemon; the power is gone
# now, but "+1" and " f" would still render a color nobody asked for. So
# validate the digits explicitly rather than relying on what int() rejects.
_HEX_DIGITS = "0123456789abcdefABCDEF"


def to_pixels(hex_str):
    """'#rrggbb' -> the byte triple the pad stores, matching the app path."""
    if not (isinstance(hex_str, str) and len(hex_str) == 7 and hex_str[0] == "#"
            and all(c in _HEX_DIGITS for c in hex_str[1:])):
        raise ValueError(f"bad hex {hex_str!r}")
    return tuple(int(hex_str[i:i + 2], 16) for i in (1, 3, 5))


# The old name, kept briefly so nothing imports a hole. It never described what
# the function does now, and it described the bug when it did.
linearize = to_pixels


def parse_spool(text):
    """-> ([(r, g, b)] * 12, hold). Raises ValueError for EVERY bad shape.

    Wrong-type inputs must not surface as TypeError: the caller only guards
    ValueError, and anything else escaping the watcher takes the daemon down.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid json: {e}")
    if not isinstance(doc, dict):
        raise ValueError("spool must be a JSON object")
    colors = doc.get("colors")
    if not (isinstance(colors, list) and len(colors) == N_KEYS):
        raise ValueError(f"colors must be exactly {N_KEYS} hex strings")
    hold = doc.get("hold")
    # bool is an int subclass; True would otherwise pass as hold=1.
    if not (isinstance(hold, int) and not isinstance(hold, bool)
            and HOLD_MIN <= hold <= HOLD_MAX):
        raise ValueError(f"hold must be an int in {HOLD_MIN}-{HOLD_MAX}")
    return [to_pixels(c) for c in colors], hold


async def watch(path, send, interval=1.0):
    """Poll the spool; on mtime change, parse and send (sync, returns bool).

    last starts at the CURRENT mtime so a pre-existing spool is treated as
    consumed (otherwise every daemon restart replays the last test frame).
    last only advances when send() reports success, so a frame written while
    the link is down is retried after reconnect.
    """
    try:
        last = os.stat(path).st_mtime
    except OSError:
        last = 0.0
    while True:
        try:
            st = os.stat(path)
            if st.st_mtime > last:
                with open(path) as f:
                    rgb, hold = parse_spool(f.read())
                frame = {"t": "ledtest", "rgb": [list(t) for t in rgb], "hold": hold}
                if send(frame):
                    last = st.st_mtime
                    # Logged because this is debug tooling with no other
                    # observable: the pad is the only feedback channel, and
                    # "did the frame leave the host?" is the first question
                    # when it looks wrong.
                    print(f"operatord: ledtest frame sent (hold={hold}s)", flush=True)
        except OSError:
            pass
        except ValueError as e:
            print(f"operatord: ledtest bad spool ignored: {e}", flush=True)
            last = st.st_mtime          # don't re-log the same bad file every tick
        await asyncio.sleep(interval)
