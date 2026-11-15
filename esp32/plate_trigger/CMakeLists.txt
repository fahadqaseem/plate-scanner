cmake_minimum_required(VERSION 3.16)
# Place this folder at esp-dev-kits/examples/esp32-p4-eye/examples/plate_trigger
# so the BSP path below resolves (same layout as factory_demo).
set(EXTRA_COMPONENT_DIRS ../common_components/esp32_p4_eye)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(plate_trigger)
