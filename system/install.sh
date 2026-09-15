#!/usr/bin/env bash
# Install macropad on this machine: firmware to the pad, daemon to systemd.
set -euo pipefail
cd "$(dirname "$0")/.."

./system/deploy-firmware.sh

mkdir -p ~/.config/systemd/user
cp system/macropad.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now macropad.service
systemctl --user --no-pager status macropad.service || true

if [ ! -e /etc/udev/rules.d/62-macropad.rules ]; then
    cat <<'EOF'

MANUAL STEP (root): install the udev rule, then replug the pad:
  sudo cp system/62-macropad.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
EOF
fi
