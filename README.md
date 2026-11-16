# Free Miles, by Team Zorro: truck license-plate scanner

A camera reads a truck's license plate, and the result appears on a web page with the photo, the plate text and a confidence score. The plate can then be matched to a registered truck.

A capture starts in one of two ways:
- **Auto mode:** hold a plate in front of the camera and it captures by itself.
- **Button mode:** press the green button on the **FREE-WILi OG**.

An **ESP32-P4-EYE** camera takes a 1920x1080 photo and sends it over USB to a laptop. The laptop reads the plate and stores the photo and the record, either in a free fake AWS or in real AWS (S3, DynamoDB, KMS). The FREE-WILi screen shows **FREE MILES / TEAM ZORRO**, and the screen and LED bar flash green when a photo is taken.

## How it works

```
FREE-WILi green button          (or: laptop timer in auto mode)
   |  USB: prints "pulse"
   v
laptop: bridge script  --- USB: sends "T" --->  ESP32-P4-EYE camera
   ^                                                |  takes photo, JPEG-encodes it
   +------------- JPEG over the same USB ----------+
   |
   |  auto mode only: skip photos with no plate in them (Apple Vision check)
   |  HTTP POST /recognize
   v
recognition backend (FastAPI) -- finds the plate, reads the text, saves image + record
   |                                (fake AWS, or real S3 + DynamoDB + KMS)
   v
web page (Live Plate Scans table, refreshes every 2 s)
```

There are no jumper wires between the boards and no Wi-Fi. The laptop connects them over two USB cables.

## Who built what

| Part | Where it lives |
|---|---|
| FREE-WILi app, ESP32 camera firmware, USB image transfer, auto-capture, demo launcher | this repo, `main` |
| Recognition backend (FastAPI, plate locator, OCR, AWS storage, bridge) | Anvita, `backend/` (from branch `add-lpr-backend`) |
| Backend fix: real confidence scores from Tesseract 5.5 | `backend/app/ocr_plate.py` (from branch `fix-ocr-confidence`) |
| Web front end | `frontend/` |

## Run the demo

Plug both boards into the laptop. The camera goes in its **Debug** USB-C port.

### Quick start: button mode, fake AWS (free, no account)

```bash
./demo/start_demo.sh
```

On the first run it creates `demo/.venv` and installs dependencies. Then it opens <http://localhost:8000/demo/>. Press the green button to scan. Records stay in memory and are lost when it stops.

### Auto mode, and real AWS

Run the API in one terminal and the auto-capture bridge in a second one:

```bash
# terminal 1: the API (add --aws to use real AWS, see below)
demo/.venv/bin/python demo/run_local_demo.py backend

# terminal 2: auto capture. The green button still works too.
demo/.venv/bin/python demo/auto_bridge.py backend --api-key demo-camera-key
```

The auto bridge takes a photo about every 2.5 s. It sends a photo to the API only when it finds plate-like text: tall enough (at least 7% of the image height), read with at least 80% confidence, and containing at least 2 digits. The same plate is ignored for 15 s. Button photos are always sent. You can change these with `--interval`, `--min-height` and `--repeat-seconds`.

**Real AWS:** create `demo/aws.env` (gitignored, never commit it) with:

```
AWS_REGION=us-east-2
S3_BUCKET=<bucket>
DYNAMODB_TABLE=lpr-records
VEHICLES_TABLE=lpr-vehicles
KMS_KEY_ID=<kms key arn>
API_KEYS=<camera key>
```

Run `aws configure` with a least-privilege IAM user, then start the API with `--aws`, and pass the same camera key to the bridge with `--api-key`. To have a scan matched to a truck, add an item to the `lpr-vehicles` table, for example `plate_number = ESB9049`, `vehicle_id = T01`.

### Plate reading

`demo/run_local_demo.py` reads plates with **Apple Vision** (macOS only, offline) and falls back to Anvita's Tesseract reader. On our test photos Apple Vision was far more accurate on decorated or angled plates. Reads below 75% confidence are shown as low confidence.

Tips for a good read: hold the plate flat, steady and in focus, about 0.5 to 1 m from the camera.

## Layout

| Path | What it is |
|---|---|
| `backend/` | Recognition API (FastAPI), bridge, AWS infrastructure, tests. See `backend/README.md` |
| `freewili/platescan/` | FREE-WILi OG app: green button trigger, FREE MILES / TEAM ZORRO screen, LED bar, flash on photo. Built on FreeWili's [wiliOGBsp](https://github.com/freewili/wiliOGBsp) |
| `esp32/plate_trigger/` | ESP-IDF 5.5 firmware for the ESP32-P4-EYE: camera, hardware JPEG encoder, USB output |
| `esp32/pin_finder/` | Diagnostic that prints which ESP GPIOs go high |
| `host/` | `receive_plates.py` (saves photos to disk, no backend needed) and `HOW_TO_GET_IMAGES.md` |
| `demo/run_local_demo.py` | Starts the backend with fake or real AWS, Apple Vision reader, serves the front end |
| `demo/auto_bridge.py` | Auto-capture bridge |
| `demo/apple_vision_ocr.py` | Apple Vision plate reader |
| `demo/start_demo.sh` | One-command demo (button mode, fake AWS) |
| `frontend/` | The web page |
| `samples/` | Test photos from the camera, plus CC0 plate photos in `samples/web/` |
| `HANDOVER.md` | Guide for someone who only needs the images |

## Build and flash the boards

Both boards are already flashed. Only do this to change the firmware.

FREE-WILi (needs Pico SDK 2.3.0 and arm-none-eabi 15.2 under `~/.pico-sdk`, see the wiliOGBsp README):
```bash
git clone https://github.com/freewili/wiliOGBsp && ./freewili/install.sh ./wiliOGBsp
cd wiliOGBsp && python3 tools/fw.py build platescan && python3 tools/fw.py flash platescan
```
`fw.py` needs `pyserial` in the Python that runs it.

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
| Auto capture | Tested. Real plates are captured, and text like keyboards and laptop logos is filtered out |
| Photo -> backend -> plate text -> web page | Tested with real plates shown on a phone screen |
| Real AWS (S3 with KMS, DynamoDB) | Tested end to end at the demo |
| `start_demo.sh` | Syntax-checked only; the demo was started with the two commands above |
| Windows and Linux | Not tested. Apple Vision is macOS only, so other systems use Tesseract |
| Plate reading on bad shots | Blur, glare and steep angles can cause misreads or an extra character |
| Backend tests | Two tests in `backend/tests` fail with Tesseract 5.5 (one existed before our fix) |

## Notes and limitations

- **FREE-WILi display:** the LCD backlight is off at boot, so the app turns it on. The BSP text driver draws up to scale 3 only, so the app draws its large text itself.
- **Camera board:** it's an original ESP32-P4 **rev v1.3**, hence `CONFIG_ESP32P4_SELECTS_REV_LESS_V3` in `sdkconfig.defaults`. Newer P4X boards need that removed.
- **Pinned dependency:** `esp_lvgl_port` is pinned to 2.6.x because 2.9.x does not compile on IDF 5.5.4.
- **Image size:** 1920x1080. The sensor rejects a forced 640x480.
- **Unused triggers:** the firmware also supports a GPIO trigger and Wi-Fi POST (off by default). The Orca module's pin mapping to the camera header was not worked out, so the USB path is the working one.
- **Recent-scans list:** the web page reads it from API memory, so it empties when the API restarts. Records in real AWS stay.
- **Privacy:** photos can show people. Samples of people in `samples/` are published with everyone's consent. Don't reuse them elsewhere. Auto mode keeps the last checked frame in `demo/.spool/` (gitignored).
- **Secrets:** never commit AWS keys, `demo/aws.env` or a `.env` file.
