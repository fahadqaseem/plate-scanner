# Getting plate images off the camera over a wire

**For:** Anvita (recognition service). **Status:** tested end to end on the real hardware: FREE-WILi green button -> camera -> JPEG saved on the laptop.

## The flow (no extra wires, only the two USB cables)

```
FREE-WILi green button
   │ USB (FREE-WILi main port prints "pulse")
   ▼
receive_plates.py (on the laptop) ── sends "T" over USB ──▶ ESP32-P4-EYE camera (USB Debug port)
   ▲                                                         │ captures + JPEG-encodes
   └──────────────── JPEG frame over the same USB cable ◀────┘
   │ saves ./plates/plate_<time>.jpg
   ▼ (optional) POST to http://localhost:8000/recognize
```

The camera is on the ESP32-P4-EYE, so images come from **its** USB cable (the **Debug** USB-C port). You do not need the `freewili` Python library or `get_file`. The FREE-WILi only announces the button press.

## Quick start

```bash
pip install pyserial requests
python receive_plates.py                     # finds both boards by USB identity
python receive_plates.py --post http://localhost:8000/recognize --api-key "<camera key>"
```

Press the green button on the FREE-WILi. A file appears in `./plates/`. The Boot button on the P4-EYE also works as a manual trigger. Sending the single byte `T` to the camera's serial port triggers a capture from any program.

The `--post` request matches your example: multipart field `file`, filename `plate_<time>.jpg`, content type `image/jpeg`, header `X-API-Key`.

## Wire format (if you want to parse it yourself)

The ESP's log text and the images share one serial port at 115200 baud. Each image is one frame:

| Bytes | Content |
|---|---|
| 8 | ASCII `PLATEJPG` |
| 4 | JPEG length N, unsigned little-endian |
| N | the JPEG file |
| 4 | CRC32 of the JPEG, unsigned little-endian (same as Python `zlib.crc32`) |

Scan the stream for the magic, read the length, then verify the CRC. Drop the frame if it fails. `FrameParser` in `receive_plates.py` does exactly this.

## Details

- Image size: 1920x1080 JPEG, quality 80, about 160-380 KB. This sensor does not accept a forced 640x480.
- Triggers within 1.5 s of the previous one are ignored.
- Only one program can open each serial port at a time. Close `idf.py monitor` first.
- Wi-Fi POST from the camera is off by default (`idf.py menuconfig` -> *Plate Trigger*).
- The camera board is an original ESP32-P4 revision v1.3, so the firmware is built with `CONFIG_ESP32P4_SELECTS_REV_LESS_V3`.

## Troubleshooting

- *Nothing saved:* check `ls /dev/cu.usb*` shows the camera, the FREE-WILi main port, and that nothing else has them open.
- *Very dark image right after power-up:* auto-exposure was still settling. Later shots are fine.
- *Camera not found:* use the **Debug** USB-C port on the P4-EYE; hold Boot and tap Reset to force download mode if needed.
