"""Main loop: input polling, link polling, single-app dispatch."""
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

from .link import Link
from .ui import Screen

BRIGHTNESS = 0.3


class App:
    name = "app"

    def attach(self, pad, link, screen):
        self.pad = pad
        self.link = link
        self.screen = screen

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def on_key_event(self, n, pressed, now):
        pass

    def on_dial(self, delta):
        pass

    def on_enc(self, pressed, now):
        pass

    def on_msg(self, msg):
        pass

    def tick(self, now):
        pass


def run(macropad, app):
    link = Link(ticks_ms, ticks_diff)
    screen = Screen(macropad.display)
    macropad.pixels.brightness = BRIGHTNESS
    app.attach(macropad, link, screen)

    ledtest_until = None       # ticks_ms deadline for a debug LED frame; None = inactive
    last_pos = macropad.encoder
    link.send({"t": "hello", "fw": "0.1.0", "app": app.name})
    try:
        app.on_show()
    except Exception as e:
        print("on_show error:", repr(e))

    while True:
        now = ticks_ms()

        macropad.encoder_switch_debounced.update()
        if macropad.encoder_switch_debounced.pressed:
            try:
                app.on_enc(True, now)
            except Exception as e:
                print("on_enc error:", repr(e))
        if macropad.encoder_switch_debounced.released:
            try:
                app.on_enc(False, now)
            except Exception as e:
                print("on_enc error:", repr(e))

        pos = macropad.encoder
        delta, last_pos = pos - last_pos, pos
        if delta:
            try:
                app.on_dial(delta)
            except Exception as e:
                print("on_dial error:", repr(e))

        event = macropad.keys.events.get()
        while event is not None:
            try:
                app.on_key_event(event.key_number, event.pressed,
                                  event.timestamp)
            except Exception as e:
                print("on_key_event error:", repr(e))
            event = macropad.keys.events.get()

        for m in link.poll(now):
            # Debug LED frame (host-side palette eyeballing). There is no
            # framework "LED pass" to skip -- apps repaint all 12 pixels every
            # tick -- but no app ever calls pixels.show(), so gating auto_write
            # freezes the visible frame while apps keep their state fresh via
            # on_msg/tick. rgb is the byte triple the app path would store: the
            # daemon does NOT gamma-decode, so this renders identically to a
            # Cockpit key of the same color (see macropadd/ledtest.py).
            if m.get("t") == "ledtest":
                try:
                    macropad.pixels.auto_write = False
                    for i, rgb in enumerate(m.get("rgb", [])[:12]):
                        macropad.pixels[i] = tuple(rgb)
                    macropad.pixels.show()
                    ledtest_until = ticks_add(now, int(m.get("hold", 30)) * 1000)
                except Exception as e:
                    # A raise here with auto_write already off would strand the
                    # LEDs frozen forever -- restore before giving up.
                    print("ledtest error:", repr(e))
                    ledtest_until = None
                    macropad.pixels.auto_write = True
                continue                       # never reaches the app
            try:
                app.on_msg(m)
            except Exception as e:
                print("on_msg error:", repr(e))

        if ledtest_until is not None and ticks_diff(now, ledtest_until) >= 0:
            ledtest_until = None
            macropad.pixels.auto_write = True
            try:
                app.on_show()     # repaint current state
            except Exception as e:
                print("on_show error:", repr(e))

        try:
            app.tick(now)
        except Exception as e:
            print("tick error:", repr(e))
