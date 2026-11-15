#!/bin/sh
# Copies the platescan app into a wiliOGBsp checkout and registers it.
# usage: ./install.sh /path/to/wiliOGBsp
set -e
BSP="${1:?usage: install.sh /path/to/wiliOGBsp}"
cp -R "$(dirname "$0")/platescan" "$BSP/apps/"
grep -q "apps/platescan" "$BSP/CMakeLists.txt" || \
  sed -i.bak 's#^add_subdirectory(apps/ogvegas)#add_subdirectory(apps/ogvegas)\nadd_subdirectory(apps/platescan)#' "$BSP/CMakeLists.txt"
echo "installed. Next: cd $BSP && python3 tools/fw.py build platescan && python3 tools/fw.py flash platescan"
