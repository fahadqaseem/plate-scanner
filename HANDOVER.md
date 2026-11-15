# Handover: how you get the plate images

Short version: **press the green button on the FREE-WILi, a JPEG file appears in a folder on the laptop.** Your recognition service takes it from there.

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

Both devices plug into the **same laptop**, each with its own USB cable:
1. **FREE-WILi OG**, with the P4-EYE camera attached through the Orca module.
2. **ESP32-P4-EYE camera**, in its **Debug** USB-C port (not the "USB 2.0" one).

There is no Wi-Fi and no extra wire between them. The laptop passes the button press from the FREE-WILi to the camera.

## Run it

```bash
git clone https://github.com/fahadqaseem/plate-scanner
cd plate-scanner
pip install pyserial requests
python host/receive_plates.py
```

It prints `camera: ... | FREE-WILi: ...` and then waits. Press the green button and you will see `saved plate_....jpg`.

## Sending the images to your service

Add `--post` and your key and every image is sent as it is captured:

```bash
python host/receive_plates.py --post http://localhost:8000/recognize --api-key "<camera key>"
```

This is the same request as your example script:

```python
requests.post("http://localhost:8000/recognize",
              headers={"X-API-Key": "<camera key>"},
              files={"file": ("plate_<time>.jpg", jpeg_bytes, "image/jpeg")})
```

The response text is printed to the terminal. To use a different flow, read the files from `plates/` yourself, or import `FrameParser` from `host/receive_plates.py`.

## Things to know

- **No FREE-WILi library is needed.** Your `get_file` / `take_picture` flow is not used. The camera is on the ESP32 board, not the FREE-WILi, and the FREE-WILi now runs a small custom app that only announces the button press.
- **Only one program can use each USB port.** Close `idf.py monitor` or any serial terminal before running the script.
- **Dark first picture after power-up:** the camera is still adjusting exposure. Later pictures are fine.
- **Manual trigger:** the Boot button on the P4-EYE also captures. A program can trigger it by sending the single byte `T` to the camera's serial port.
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
