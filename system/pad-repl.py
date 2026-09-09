#!/usr/bin/env python3
"""Drive the pad's CircuitPython REPL over /dev/operator-repl.

The REPL channel is the only control path that survives the CIRCUITPY drive
being hidden (see firmware/boot.py), so deploys go through here. The daemon
owns the *data* channel, not this one, so nothing contends for it.

Commands:
  pause   stop code.py and turn auto-reload off, so an rsync cannot race it
  reload  Ctrl-D, one clean soft reload (does NOT re-run boot.py)
  expose  write /.expose-drive and hard reset, so the next boot shows the drive
  hide    hard reset with no marker, so boot.py hides the drive again

A reset is confirmed, not assumed: writing the bytes is not the same as the pad
acting on them (bench 2026-09-05 -- a `hide` issued right after the host
unmounted the FAT returned success while the pad kept running, leaving the
drive exposed). Both reset commands wait for the serial port to disappear,
which only happens when USB genuinely re-enumerates.

Exit status is 1 on any failure; callers decide whether that is fatal.
"""
import argparse
import os
import sys
import time

import serial

PORT = os.environ.get("OPERATOR_REPL", "/dev/operator-repl")
BAUD = 115200
RESET = "import microcontroller; microcontroller.reset()"


def wait_for_port(timeout, want=True):
    """Block until the device node exists (or is gone). Returns success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(PORT) == want:
            if want:
                # udev has made the symlink, but the tty can need another
                # moment before it accepts an open().
                time.sleep(0.5)
            return True
        time.sleep(0.1)
    return False


def enter_repl(port):
    """Interrupt code.py and land at the >>> prompt.

    The settle is load-bearing. CircuitPython's USB CDC discards input written
    before it notices the host has opened the port, so a Ctrl-C sent the
    instant after open() is simply lost -- which is why a reset issued right
    after the host unmounted the FAT failed on every first try (bench
    2026-09-05) and only landed on the retry.
    """
    time.sleep(0.5)
    port.write(b"\x03")  # Ctrl-C: stop code.py
    time.sleep(0.5)
    port.write(b"\r")  # any key: enter the REPL
    time.sleep(0.5)
    port.reset_input_buffer()


def send(port, line):
    port.write(line.encode() + b"\r")
    time.sleep(0.5)


def reset_and_confirm(prepare=None, attempts=1):
    """Hard reset the pad and verify it really happened.

    `prepare` runs at the REPL first. Poll fast for the port to vanish: if a
    reset were missed while the node was only briefly absent, a retry would
    re-run `prepare` against an unexpected state, so err toward detecting it.
    """
    for attempt in range(1, attempts + 1):
        with serial.Serial(PORT, BAUD, timeout=2) as port:
            enter_repl(port)
            if prepare is not None:
                prepare(port)
            send(port, RESET)
        if wait_for_port(10, want=False):
            return True
        print(
            f"pad-repl: pad did not re-enumerate (attempt {attempt}/{attempts})",
            file=sys.stderr,
        )
    return False


def write_marker(port):
    # Legal only while the drive is hidden: with USB mass storage live the host
    # owns the FAT and CircuitPython refuses the remount.
    send(port, "import storage; storage.remount('/', readonly=False)")
    send(port, "open('/.expose-drive', 'w').close()")
    send(port, "import storage; storage.remount('/', readonly=True)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pause", "reload", "expose", "hide"))
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="seconds to wait for the port to appear before giving up",
    )
    args = parser.parse_args()

    if args.wait and not wait_for_port(args.wait):
        print(f"pad-repl: {PORT} did not appear within {args.wait:g}s", file=sys.stderr)
        return 1
    if not os.path.exists(PORT):
        print(f"pad-repl: {PORT} missing", file=sys.stderr)
        return 1

    if args.command == "expose":
        # One attempt only. A retry would re-run write_marker(), and if the
        # first reset did land the drive is now exposed, the remount fails, and
        # the retry would reset the pad straight back into hidden. The caller
        # checks for the drive itself, which is the honest signal anyway.
        return 0 if reset_and_confirm(write_marker) else 1
    if args.command == "hide":
        # Idempotent: no marker, nothing to undo, so retrying is safe.
        return 0 if reset_and_confirm(attempts=2) else 1

    with serial.Serial(PORT, BAUD, timeout=2) as port:
        if args.command == "reload":
            port.write(b"\x04")  # Ctrl-D: one clean soft reload
        else:  # pause
            enter_repl(port)
            # autoreload is not persistent, so the next reload restores it.
            send(port, "import supervisor; supervisor.runtime.autoreload = False")
    return 0


if __name__ == "__main__":
    sys.exit(main())
