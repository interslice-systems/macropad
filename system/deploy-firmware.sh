#!/usr/bin/env bash
# Deploy firmware/ to the CIRCUITPY drive and shared/km_*.py into its lib/.
#
# firmware/boot.py hides the CIRCUITPY drive in normal operation, so the drive
# usually is not there when this runs. Getting it back is step 1 below: ask the
# pad over its REPL to leave a one-shot marker and hard reset. If that path is
# broken, hold key 1 (top-left) while replugging and re-run -- the script takes
# the already-exposed branch and behaves as it did before the drive was hidden.
#
# CircuitPython's auto-reload restarts the pad ~1 s after the first write
# lands, which can re-enumerate USB mid-rsync (bench 2026-08-16: interrupted
# renames -> kernel remounted the FAT read-only -> 30 s USB reset loop).
# So: pause auto-reload over the REPL channel, copy everything, then hand the
# pad back (supervisor.runtime.autoreload is not persistent, so any reset or
# reload restores it).
set -euo pipefail
cd "$(dirname "$0")/.."

REPL=system/pad-repl.py
DISK=/dev/disk/by-label/CIRCUITPY

fail() {
    echo "ERROR: $1" >&2
    echo "       Hold key 1 (top-left) while replugging the pad, then re-run." >&2
    exit 1
}

wait_for_disk() {   # $1 = "present" | "gone"
    local want=$1 deadline=$((SECONDS + 25))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if [ "$want" = present ]; then
            [ -e "$DISK" ] && return 0
        else
            [ -e "$DISK" ] || return 0
        fi
        sleep 0.5
    done
    return 1
}

# --- 1. Get a writable CIRCUITPY ----------------------------------------
# Two attempts, because an interrupted earlier deploy can leave the FAT dirty:
# the kernel's errors=remount-ro fires and every write fails. Unmounting and
# remounting does NOT clear that (bench 2026-09-05) -- the pad has to re-present
# the medium, so the recovery is a hard reset back to the hidden state, then a
# fresh expose. That is exactly what a second pass through this loop does.
MP=""
exposed_by_us=0
for attempt in 1 2; do
    if [ ! -e "$DISK" ]; then
        echo "CIRCUITPY hidden; asking the pad to expose it for one boot"
        python3 "$REPL" expose --wait 10 || fail "could not reach the pad's REPL"
        wait_for_disk present || fail "CIRCUITPY did not appear after the reset"
        exposed_by_us=1
    fi

    MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY || true)
    if [ -z "$MP" ]; then
        udisksctl mount -b "$DISK" >/dev/null
        MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY)
    fi

    if touch "$MP/.deploy-probe" 2>/dev/null; then
        rm -f "$MP/.deploy-probe"
        break
    fi

    MP=""
    [ "$attempt" = 2 ] && fail "CIRCUITPY stayed read-only after a reset"
    echo "CIRCUITPY is read-only (dirty FAT); resetting the pad and retrying"
    udisksctl unmount -b "$DISK" >/dev/null || true
    python3 "$REPL" hide --wait 10 || fail "could not reset the pad"
    wait_for_disk gone || true   # a pad without boot.py stays exposed; fine
    exposed_by_us=0
done
[ -n "$MP" ] || fail "no writable CIRCUITPY"

# --- 2. Stop the pad writing under us -----------------------------------
repl_ok=0
if python3 "$REPL" pause --wait 15; then
    repl_ok=1
else
    echo "WARN: REPL pause failed; deploying with autoreload live"
fi

# --- 3. Copy -------------------------------------------------------------
# 'P km_*.py' protects the shared modules (synced below) from --delete: they
# don't live under firmware/, so without protection this pass would see them
# as absent from the source and delete them before the next line gets a
# chance to put them back. '.expose-drive' is the pad's own marker, and this
# script can be running precisely because boot.py has not consumed it yet.
rsync -r --delete --filter='P km_*.py' --filter='P .expose-drive' \
    --exclude backup-factory --exclude __pycache__ firmware/ "$MP"/
# A plain `cp` here only ever ADDS or updates files -- it can't remove a
# shared module that was deleted from shared/, so a retired km_*.py (e.g.
# km_text.py) would survive on CIRCUITPY forever. Scope --delete to exactly
# the km_*.py pattern so this pass only ever touches the files it owns and
# leaves the rest of lib/ (the third-party adafruit_* packages) alone.
rsync -r --delete --include='km_*.py' --exclude='*' \
    --exclude __pycache__ shared/ "$MP/lib/"
sync
echo "deployed to $MP"

# --- 4. Hand the pad back ------------------------------------------------
if [ "$exposed_by_us" = 1 ]; then
    # Unmount BEFORE the reset. Resetting while the host still holds the FAT
    # is the corruption this script exists to avoid.
    udisksctl unmount -b "$DISK" >/dev/null
    if python3 "$REPL" hide && wait_for_disk gone; then
        echo "drive hidden again"
    else
        echo "WARN: the drive is still exposed. Press the pad's reset button." >&2
    fi
else
    # The drive was already exposed when this started -- a held key 1, a pad
    # with no boot.py yet, or an interrupted deploy. Leave it exposed: the
    # first two are deliberate, and hiding it would fight the person doing it.
    # Say so, because otherwise the state is sticky and silent.
    if [ "$repl_ok" = 1 ]; then
        python3 "$REPL" reload || echo "WARN: reload failed; press the pad's reset button once"
    else
        echo "NOTE: autoreload was live during this deploy; if the pad wedged, replug it"
    fi
    echo "NOTE: CIRCUITPY was already exposed and stays exposed."
    echo "      To hide it: udisksctl unmount -b $DISK && python3 $REPL hide"
fi
