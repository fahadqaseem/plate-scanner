#!/bin/sh
# Copies plate_trigger into an esp-dev-kits checkout so its BSP path resolves.
# usage: ./install.sh /path/to/esp-dev-kits
set -e
DK="${1:?usage: install.sh /path/to/esp-dev-kits}"
cp -R "$(dirname "$0")/plate_trigger" "$DK/examples/esp32-p4-eye/examples/"
cp -R "$(dirname "$0")/pin_finder" "$DK/examples/esp32-p4-eye/examples/"
echo "installed. Next: cd $DK/examples/esp32-p4-eye/examples/plate_trigger && idf.py set-target esp32p4 && idf.py build flash"
