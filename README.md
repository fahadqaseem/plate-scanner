# Team Zorro: truck license-plate scanner

A hardware button that photographs a truck's license plate, reads the plate, and shows the result on a web page.

Press the green button on a **FREE-WILi OG**. An **ESP32-P4-EYE** camera takes a 1920x1080 photo and sends it over USB to a laptop. A recognition backend reads the plate and a web page lists every scan with the photo, the plate text and a confidence score. A truck's plate can then be matched to a registered vehicle.

## How it works

```
FREE-WILi green button
   |  USB: prints "pulse"
   v
laptop: bridge script  --- USB: sends "T" --->  ESP32-P4-EYE camera
   ^                                                |  takes photo, JPEG-encodes it
   +------------- JPEG over the same USB ----------+
   |
   |  HTTP POST /recognize
   v
recognition backend (FastAPI) -- finds the plate, reads the text, stores image + record
   |
   v
web page (Live Plate Scans table, refreshes every 2 s)
```

There are no jumper wires between the boards and no Wi-Fi. The laptop connects them over two USB cables. The FREE-WILi screen and LED bar flash green when a photo is taken.

## Who built what

| Part | Where it lives |
|---|---|
| FREE-WILi app, ESP32 camera firmware, USB image transfer, demo glue | this repo (`main`) |
| Recognition backend (FastAPI, plate locator, OCR, AWS storage, bridge) | Anvita's branch `add-lpr-backend` |
| Web front end | `frontend/` |

## Run the demo (free, no AWS account needed)

You need both boards plugged into the laptop (camera in its **Debug** USB-C port) and the backend code from the `add-lpr-backend` branch checked out next to this repo.

```bash
./demo/start_demo.sh /path/to/lpr-backend/backend
```

Then open <http://localhost:8000/demo/> and press the green button on the FREE-WILi.

What it does:
- Starts Anvita's API on port 8000 with a **fake AWS** (an in-memory stand-in), so it costs nothing. Records and photos are lost when it stops.
- Reads plates with **Apple Vision** (macOS, offline) and falls back to Anvita's Tesseract reader. On our test photos the Apple reader was far more accurate.
- Serves the front end from the same address and runs the bridge that connects the boards to the API.

Tips for a good read: hold the plate flat, steady and in focus, about 0.5 to 1 m from the camera.

## Layout

| Path | What it is |
|---|---|
| `freewili/platescan/` | FREE-WILi OG app: green button sends the trigger, LED bar and screen flash. Built on FreeWili's [wiliOGBsp](https://github.com/freewili/wiliOGBsp) |
| `esp32/plate_trigger/` | ESP-IDF 5.5 firmware for the ESP32-P4-EYE: camera, hardware JPEG encoder, USB output |
| `esp32/pin_finder/` | Diagnostic that prints which ESP GPIOs go high |
| `host/` | `receive_plates.py` (saves photos to disk, simple alternative to the bridge) and `HOW_TO_GET_IMAGES.md` |
| `demo/` | Local demo launcher, Apple Vision reader, `start_demo.sh` |
| `frontend/` | The web page |
| `samples/` | Test photos from the camera, plus CC0 plate photos in `samples/web/` |
| `HANDOVER.md` | Step-by-step guide for the person consuming the images |

## Build and flash the boards

Both boards are already flashed. Only do this to change the firmware.

FREE-WILi (needs Pico SDK 2.3.0 and arm-none-eabi 15.2 under `~/.pico-sdk`, see the wiliOGBsp README):
```bash
git clone https://github.com/freewili/wiliOGBsp && ./freewili/install.sh ./wiliOGBsp
cd wiliOGBsp && python3 tools/fw.py build platescan && python3 tools/fw.py flash platescan
```

ESP32-P4-EYE (ESP-IDF v5.5.x; use the **Debug** USB-C port):
```bash
git clone --depth 1 https://github.com/espressif/esp-dev-kits && ./esp32/install.sh ./esp-dev-kits
cd esp-dev-kits/examples/esp32-p4-eye/examples/plate_trigger
idf.py set-target esp32p4 && idf.py build && idf.py -p <port> flash
```

Save photos without the backend:
```bash
pip install pyserial requests
python host/receive_plates.py            # photos go to ./plates/
```

## Status

| | |
|---|---|
| Button -> camera -> photo on the laptop | Tested on real hardware (macOS) |
| Photo -> backend -> plate text -> web page | Tested with real plates shown on a phone screen |
| Real AWS (S3, DynamoDB, KMS) | Stack deployed by Anvita, **not connected to our demo**. The demo uses a fake AWS |
| Windows and Linux | Not tested |
| Plate reading on bad shots | Blur, glare and steep angles can cause misreads or an extra character |

## Notes and limitations

- The camera board is an original ESP32-P4 **rev v1.3**, hence `CONFIG_ESP32P4_SELECTS_REV_LESS_V3` in `sdkconfig.defaults`. Newer P4X boards need that removed.
- `esp_lvgl_port` is pinned to 2.6.x because 2.9.x does not compile on IDF 5.5.4.
- Images are 1920x1080. The sensor rejects a forced 640x480.
- The firmware also supports a GPIO trigger and Wi-Fi POST (off by default). The Orca module's pin mapping to the camera header was not worked out, so the USB path is the working one.
- Photos can show people. Samples of people in `samples/` are published with everyone's consent. Don't reuse them elsewhere.
- Never commit AWS keys or a `.env` file.
