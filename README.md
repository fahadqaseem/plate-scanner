# Plate scanner: FREE-WILi button -> ESP32-P4-EYE camera -> laptop

Press the green button on a FREE-WILi OG. The ESP32-P4-EYE takes a JPEG and sends it
to the laptop over USB. A script saves it and can POST it to a recognition service.
No jumper wires and no Wi-Fi are needed.

```
FREE-WILi green button --USB--> host/receive_plates.py --USB "T"--> ESP32-P4-EYE camera
                                      ^                                   |
                                      +----------- JPEG over USB ---------+
```

## Layout
- `freewili/platescan/`  FREE-WILi OG app (display CPU sends the button event, main CPU announces it). Built with FreeWili's [wiliOGBsp](https://github.com/freewili/wiliOGBsp).
- `esp32/plate_trigger/` ESP-IDF 5.5 firmware for the ESP32-P4-EYE (camera, hardware JPEG, USB output).
- `esp32/pin_finder/`    Diagnostic: prints which ESP GPIOs go high.
- `host/`                `receive_plates.py` and `HOW_TO_GET_IMAGES.md` (the guide for whoever consumes the images).

## Build and flash
FREE-WILi (needs Pico SDK 2.3.0 + arm-none-eabi 15.2 under `~/.pico-sdk`, see the wiliOGBsp README):
```bash
git clone https://github.com/freewili/wiliOGBsp && ./freewili/install.sh ./wiliOGBsp
cd wiliOGBsp && python3 tools/fw.py build platescan && python3 tools/fw.py flash platescan
```
Add `-DPLATESCAN_SELFTEST=ON` to the cmake configure for a self-test that pulses without a button.

ESP32-P4-EYE (ESP-IDF v5.5.x; plug into the **Debug** USB-C port):
```bash
git clone --depth 1 https://github.com/espressif/esp-dev-kits && ./esp32/install.sh ./esp-dev-kits
cd esp-dev-kits/examples/esp32-p4-eye/examples/plate_trigger
idf.py set-target esp32p4 && idf.py build && idf.py -p <port> flash
```

Run on the laptop:
```bash
pip install pyserial requests
python host/receive_plates.py [--post http://localhost:8000/recognize --api-key KEY]
```

## Notes
- The tested board is an original ESP32-P4 **rev v1.3**, hence `CONFIG_ESP32P4_SELECTS_REV_LESS_V3` in `sdkconfig.defaults`. Newer P4X boards need that removed.
- `esp_lvgl_port` is pinned to 2.6.x because 2.9.x does not compile on IDF 5.5.4.
- Images are 1920x1080. The sensor rejects a forced 640x480.
- The ESP also supports a GPIO trigger and Wi-Fi POST (off by default, `idf.py menuconfig` -> Plate Trigger). The Orca module's pin mapping to the camera header was not determined, so the USB path is the working one.
- Not tested: the `--post` step against a real recognition endpoint.
