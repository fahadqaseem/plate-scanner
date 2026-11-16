#!/bin/sh
# Copies the platescan app into a wiliOGBsp checkout and registers it.
# usage: ./install.sh /path/to/wiliOGBsp
set -e
BSP="${1:?usage: install.sh /path/to/wiliOGBsp}"
cp -R "$(dirname "$0")/platescan" "$BSP/apps/"
if ! grep -q "apps/platescan" "$BSP/CMakeLists.txt"; then
  # portable (macOS and Linux): add our line right after the ogvegas app
  awk '{print} /^add_subdirectory\(apps\/ogvegas\)/{print "add_subdirectory(apps/platescan)"}' \
    "$BSP/CMakeLists.txt" > "$BSP/CMakeLists.txt.tmp" && mv "$BSP/CMakeLists.txt.tmp" "$BSP/CMakeLists.txt"
fi
echo "installed. Next: cd $BSP && python3 tools/fw.py build platescan && python3 tools/fw.py flash platescan"
