# macropad

CircuitPython firmware and a small host daemon that turn an
[Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a desk
companion for an [Omarchy](https://omarchy.org) / Hyprland desktop: a physical
switchboard of open terminal windows, dressed in whatever Omarchy theme is
active. Desktop stand here: https://www.printables.com/model/1838935-adafruit-macropad-rp2040-stand

Previously called **Keymaker**, then **Operator** (renamed 2026-09-15).
Historical design documents keep those names; the checkout, daemon and user
service are now `macropad`, `macropadd` and `macropad.service`. The shared `km_*` modules retain their wire-compatible
internal names.

## What it does

A single app: **Cockpit**, the split deck.

- **Top six keys = Hyprland workspaces 1–6**, each lit in its workspace's
  colorhash color (same Petroff-10 palette as the tmux status bar): active
  full-bright, occupied dimmed, urgent pulsing, empty dark. Tap to switch
  workspace; hold to move the focused window there silently.
- **Bottom six keys = windows on the active workspace**: the tmux windows of
  sessions associated with local terminal windows first (each in the colorhash
  color of its window NAME, the same cell the tmux status bar paints), then
  unassociated terminals (which have no stable name, so they take the cell of
  the key they land on). `tmux-local-clients` is the association authority, so
  ordinary Kitty, foot, and Ghostty windows work without special classes or
  launchers. Focused full-bright, others dimmed, a bell blinks the key's own hue. Tap to jump to
  that window.
- **The OLED is a radio.** A stereo faceplate with a framed 16-band
  segmented audio spectrum, track/artist text, a frequency legend and
  one-second peak-hold caps. It is always on: silence just leaves the bars
  flat. Audio comes from the default PipeWire output (including Lofi
  Girl/mpv); no microphone or player-specific plugin is needed. REC and
  submap badges overlay it while the screen is being captured or a submap
  is active. (The digital rain, the bell wall and the workspace-switch
  numeral were retired 2026-09-13: the deck's blinking keys already carry
  bells, and the display had a better job.)
- Urgency runs BEL through tmux and the local terminal/compositor to the pad,
  entirely in-band, so it works the same over mosh as it does locally. For an
  associated terminal, tmux's per-window bell replaces the coarse terminal
  bell only when their exact Hyprland addresses match; this prevents duplicate
  indications without hiding bells from unassociated terminals.
- **The knob is a volume knob.** Each detent moves the output volume 2%
  through `omarchy-audio-output-volume`, the script behind the keyboard's
  volume keys, so it hits the same sink and shows the same OSD. A fast spin
  coalesces instead of racing. Pushing the knob toggles
  [interslice.lofi](https://github.com/interslice-systems/lofi): stop if a
  stream is playing, else start the last-played one (`lofi toggle`). The
  media keys step between streams; the pad only reports detents and the push.

### Theme following

The daemon watches Omarchy's active theme (`colors.toml`) and pushes the
palette to the pad, which re-skins keys and screens within a couple of
seconds of a theme switch. No hooks, no templates — one file, one path, both
stable across Omarchy 3.x and 4.x.

## How it works

Two programs, one protocol, each side optional to the other:

- **Firmware** (CircuitPython 10.x + `adafruit_macropad`) owns everything
  latency-critical or standalone: drawing, LEDs, key handling. Unplug the
  daemon and the pad keeps its faceplate up with a small `no link` tag
  instead of pretending.
- **Daemon** (`macropadd`, Python ≥3.11, stdlib + pyserial) owns everything
  host-shaped: Hyprland state in and actuation out (via Hyprland's IPC
  sockets — no synthetic keystrokes), tmux window state, and the Omarchy
  palette.
- **Link**: JSON-lines over the `usb_cdc` data channel (the second CDC
  serial interface; the REPL stays free on the first). The protocol carries
  state, not commands, and the daemon re-sends a full snapshot on every
  connect — so restarts, firmware reloads, and replugs all self-heal.

The split deck's original design lives in
[docs/specs/2026-08-15-cockpit-v2-design.md](docs/specs/2026-08-15-cockpit-v2-design.md)
(the 2026-08-22 switchboard-operator spec describes a sticky-slot design that
was tried and reverted the same day); the retired weather display (rain, bell
wall, marquee) is kept as history in
[docs/specs/2026-08-23-oled-weather-design.md](docs/specs/2026-08-23-oled-weather-design.md).

## Requirements

- Adafruit MacroPad RP2040 flashed with CircuitPython 10.2.x
- Linux host running Omarchy (3.x or 4.x) with Hyprland
- Python ≥3.11 and `python-pyserial` on the host
- `cava` with PipeWire support for the audio spectrum (`omarchy pkg add cava`);
  without it the faceplate and controls continue working
- One terminal-emulator process per window. Kitty single-instance mode, foot
  server mode, Ghostty single-instance mode, and equivalent shared-process
  arrangements cannot be associated reliably and are unsupported.

## Install

```sh
git clone https://github.com/interslice-systems/macropad.git
cd macropad
./system/install.sh   # rsyncs firmware to CIRCUITPY, installs the user unit
```

The installer prints one manual step: a udev rule (stable
`/dev/macropad-*` names, and it keeps ModemManager off the serial ports)
that needs root to place. A stock pad still shows its CIRCUITPY drive, so the
first install works before the rule is in place; every later deploy needs it.

## Audio spectrum

`macropadd` supervises a headless CAVA child using `daemon/macropadd/cava.conf`.
It monitors the default PipeWire output passively, mixes left/right for display,
analyses 50 Hz–16 kHz, and sends 16 levels at 20 fps over the existing CDC link.
The pad draws 16 columns of two-pixel segments and one-pixel peak caps inside
a rounded 128-pixel bezel. Eight segment rows map the 16 incoming levels onto
a 32-pixel analyzer area; a 50/250/1K/4K/16K legend completes the
stereo faceplate. Caps
hold for 1000 ms, then drop one wire level every 80 ms (one visible segment
per two levels). This is an automatically
scaled music visualizer, not a calibrated dB meter.

The faceplate is the base layer; only REC/submap badges sit above it. The
bars go flat after two seconds with no visible audio energy, within 1.5
seconds of missing spectrum packets, or on link loss. CAVA is stopped while the pad is disconnected and restarted after failure;
its stderr goes to `journalctl --user -u macropad.service`. No new user unit or
personal CAVA config is needed. `systemctl --user restart macropad` reloads host
changes; renderer changes also require `system/deploy-firmware.sh`.

Protocol: `{"t":"spectrum","active":true,"bars":[...16 integers 0–16...]}`.
Active frames refresh freshness even when unchanged; silence sends one inactive
transition. Partial CAVA frames retain alignment, old complete frames are
coalesced, and decorative serial packets drop when the output queue backs up.
Firmware builds four tiny bar tiles and a 5×7 text font once, and only writes changed cells, at 20 fps;
an unchanged frame does no drawing. Hardware smoothness and physical
key response still require a bench check; host tests cannot establish those.

### Now playing

The two rows above the analyzer show title and artist, in a fixed 5×7 pixel
font (20 characters per row). Long lines advance in word-aware held pages
every three seconds; no sliding text. `macropadd.media` reads MPRIS through
`busctl` every two seconds while connected. mpv, Firefox and other MPRIS
players require no additional plugin or Python dependency.

Only a **playing** player with a title qualifies. The current source stays
selected while it plays; when several start together, bus-name order breaks
the tie. MPRIS does not associate a track with a PipeWire output: simultaneous
players or audio routed to a different sink can make metadata differ from the
analyzed mix. Missing metadata shows `SYSTEM AUDIO` over a blank row; an absent
artist leaves the second row blank. Metadata is ASCII-normalized, bounded to 96 characters per
field and refreshed as a heartbeat; 6.5 seconds without an update clears it.

Protocol: `{"t":"media","title":"So What","artist":"Miles Davis"}`.
The packet is included in the reconnect snapshot. The knob push's `tune`
packet (`{"t":"tune","title":...,"line":...,"hold":seconds}`) overrides both
lines for `hold` seconds — `LOADING` when the push starts Lofi Girl, `STOPPING`
under the playing title when it stops — and a new title from the player cancels
it early, so `LOADING` clears the moment the stream is audible.

Open [the animated OLED preview](docs/stereo-preview.html) locally in a browser.
It uses the same font and bezel pixels as firmware, with editable title/artist,
Lofi/jazz/fallback presets, animation pause and a 1:1 pixel view. Audio in the
preview is simulated. Regenerate it with `python tools/stereo-preview.py` after
changing `shared/km_stereo.py`.

## The CIRCUITPY drive is hidden

`firmware/boot.py` calls `storage.disable_usb_drive()`, so the pad does not
appear as removable storage. That keeps it off the desktop and it removes the
deploy hazard in [docs/pad-timing.md](docs/pad-timing.md) — with no host mount,
an rsync can no longer race CircuitPython's auto-reload into a read-only FAT.

`./system/deploy-firmware.sh` gets in on its own: it asks the pad over the REPL
serial channel to leave a `/.expose-drive` marker and hard reset. The next boot
sees the marker, consumes it, and shows the drive for that one boot; the script
copies, unmounts, and resets again to hide it. Deploying is still one command,
and takes about 30 seconds for the two USB re-enumerations.

Three ways back in if that path breaks, in order of reach:

| Route | How | Gets you |
|---|---|---|
| Escape key | hold key 1 (top-left) while plugging in | the drive, this boot |
| Safe mode | tap reset during the boot LED flash | no `boot.py`, no `code.py` |
| BOOTSEL | hold BOOTSEL while plugging in | the ROM bootloader, reflash |

So `boot.py` cannot lock you out of the pad.

## Status

Early development. Built in the open for one specific desk — an Omarchy box
named `nexus` — with its Omarchy-4 forward compatibility taken seriously and
its portability incidental. If it works on your MacroPad too, that's a happy
accident, though issues and reports are welcome.

## License

MIT.
