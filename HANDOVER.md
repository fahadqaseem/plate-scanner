# Handover: how you get the plate images

Short version: **press the green button on the FREE-WILi, a JPEG file appears in a folder on the laptop.** Your recognition service takes it from there.

## Checklist (read this first)

**You need**
- [ ] The two boards from Fahad: the **FREE-WILi** (with the Orca module and camera attached) and the **ESP32-P4-EYE camera**.
- [ ] Two USB-C/USB cables that carry data (charge-only cables will not work), both plugged into **your** laptop.
- [ ] Python 3 with `pip install pyserial requests`.
- [ ] Your recognition endpoint URL and API key, only if you want `--post`.

**You do not need** ESP-IDF, the Pico SDK, the `freewili` library, any flashing, or Wi-Fi. The boards are already programmed.

**Steps**
1. Plug the camera's **Debug** USB-C port and the FREE-WILi into your laptop. Close anything else using them.
2. Download the one file you need (no clone required):
   `curl -O https://raw.githubusercontent.com/fahadqaseem/plate-scanner/main/host/receive_plates.py`
3. `python receive_plates.py` (add `--post <url> --api-key <key>` to forward images).
4. Press the **green** button on the FREE-WILi. A JPEG appears in `plates/`.

If a step fails, jump to "If something is wrong" at the bottom, then ask Fahad.

## What you receive

| | |
|---|---|
| Output | One JPEG file per button press |
| Where | `plates/` folder next to wherever you run the script |
| Name | `plate_YYYYMMDD_HHMMSS.jpg` (local time of capture) |
| Size | 1920x1080, roughly 160-380 KB |
| Rate | Presses less than 1.5 s apart count as one |

The files are normal JPEGs. Open them with any viewer.

## What has to be connected

Which devices go into her laptop depends on how she wants to trigger a capture:

| Setup | Plug into her laptop | Trigger |
|---|---|---|
| **Full pipeline (recommended)** | Camera **and** FREE-WILi | Green button on the FREE-WILi |
| **Camera only** | Camera | Her code sends the byte `T` to the camera's serial port (tested). The Boot button is also wired as a trigger but **untested**. |

The FREE-WILi is only the physical trigger. It does no image work, so the ML side does not need it. The laptop connects the two, which is why both USB cables must go to the **same** laptop in the full setup.

Cables:
- **Camera:** the ESP32-P4-EYE's **Debug** USB-C port (not the "USB 2.0" one).
- **FREE-WILi:** its own USB cable, with the P4-EYE attached through the Orca module.

There is no Wi-Fi and no extra wire between the boards. Both boards already have their firmware stored, so nothing needs flashing.

## Software she needs

On her laptop only:

| Needed | Notes |
|---|---|
| Python 3 | Tested with 3.13 on macOS. Nothing version-specific is used. |
| `pyserial` | `pip install pyserial` (finds the boards and reads the images) |
| `requests` | `pip install requests` (only for `--post`) |
| `receive_plates.py` | The only file required. Download it as above; cloning the repo is optional. |

She does **not** need ESP-IDF, the Pico SDK, the `freewili` Python library, a compiler or any flashing tool. Those are only for rebuilding the firmware (see `README.md`).

Operating system: tested on macOS, where no driver is needed. Windows and Linux are untested. On Windows the ESP may need its USB serial driver.

For the ML model she can use whatever she likes (PIL, OpenCV, PyTorch...). The images are plain JPEG files.

## Run it

```bash
curl -O https://raw.githubusercontent.com/fahadqaseem/plate-scanner/main/host/receive_plates.py
pip install pyserial requests
python receive_plates.py
```

No `git clone` is needed. Clone the repo only if you want the README, this guide's sources, or the firmware (for reflashing). In a clone the script is at `host/receive_plates.py`.

It prints `camera: ... | FREE-WILi: ...` and then waits. Press the green button and you will see `saved plate_....jpg`. Images go to a `plates/` folder in the directory you ran it from.

## Feeding an ML model

The simplest hand-off: run the script in one terminal, and let the model code watch the `plates/` folder. Each new file is one capture.

```python
# Example only (not part of the repo): process each new image once.
from pathlib import Path
import time
from PIL import Image

seen = set()
while True:
    for f in sorted(Path("plates").glob("plate_*.jpg")):
        if f not in seen:
            seen.add(f)
            img = Image.open(f)          # 1920x1080 RGB (if it errors, the file was mid-write: retry)
            # run the model here
    time.sleep(0.2)
```

Alternatively `--post` sends each image to an HTTP endpoint (below), or she can import `FrameParser` from `receive_plates.py` to get the JPEG bytes directly.

## Sending the images to your service

Add `--post` and your key and every image is sent as it is captured:

```bash
python receive_plates.py --post http://localhost:8000/recognize --api-key "<camera key>"
```

This is the same request as your example script:

```python
requests.post("http://localhost:8000/recognize",
              headers={"X-API-Key": "<camera key>"},
              files={"file": ("plate_<time>.jpg", jpeg_bytes, "image/jpeg")})
```

The response text is printed to the terminal. To use a different flow, read the files from `plates/` yourself, or import `FrameParser` from `receive_plates.py`.

## Test images

`samples/` has 5 real captures from this camera (1920x1080 JPEG, same format the script saves), so you can test your pipeline before you have the hardware in hand. They show people and a desk, **not license plates**. Everyone in them agreed to be published. Please do not reuse them outside this project.

## Things to know

- **No FREE-WILi library is needed.** Your `get_file` / `take_picture` flow is not used. The camera is on the ESP32 board, not the FREE-WILi, and the FREE-WILi now runs a small custom app that only announces the button press.
- **Only one program can use each USB port.** Close `idf.py monitor` or any serial terminal before running the script.
- **Dark first picture after power-up:** the camera is still adjusting exposure. Later pictures are fine.
- **Manual trigger:** a program can trigger a capture by sending the single byte `T` to the camera's serial port (tested). The Boot button on the P4-EYE is wired as a trigger too but has not been tested.
- **Privacy:** the camera captures whatever it faces, including people and screens. Delete test images before sharing them.

## Status

| | |
|---|---|
| Button -> camera -> file on laptop | Tested on real hardware (macOS) |
| `--post` to your service | **Not tested.** I did not have your URL or key. |
| Windows / Linux | Not tested. Port names will differ; the script finds the boards by USB ID. |
| Image size | 1920x1080. The sensor rejected a forced 640x480; tell me if you need smaller. |

## If something is wrong

- *Script says camera not found:* check the Debug cable and run `ls /dev/cu.usb*` (macOS). Hold Boot and tap Reset on the camera if it still won't appear.
- *Button does nothing:* the script shows `FREE-WILi: not found` when that board is missing. Replug it and restart the script.
- *Nothing saved but the camera logs show a capture:* another program has the port open.

Rebuilding or reflashing either board: see `README.md`. Details of the data format: `host/HOW_TO_GET_IMAGES.md`.
