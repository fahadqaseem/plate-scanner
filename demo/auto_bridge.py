#!/usr/bin/env python3
"""Auto-capture bridge: take a photo every couple of seconds, keep only photos with a plate.

Reuses Anvita's bridge (backend/bridge/freewili_bridge.py) unchanged. Adds:
  * a timer that triggers the camera (the same "T" byte the green button causes)
  * a laptop-side plate check (Apple Vision): frames with no plate are dropped, never uploaded
  * a duplicate filter: the same plate is not saved again for --repeat-seconds
The FREE-WILi green button still works and always saves its photo.

    API_URL=http://localhost:8000 API_KEY=<camera key> python demo/auto_bridge.py /path/to/lpr-backend/backend
"""
import argparse, os, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("backend", type=Path, help="path to lpr-backend/backend")
ap.add_argument("--interval", type=float, default=2.5, help="seconds between automatic photos")
ap.add_argument("--repeat-seconds", type=float, default=15.0, help="ignore the same plate for this long")
ap.add_argument("--min-height", type=float, default=0.07,
                help="plate characters must be at least this fraction of the image height (filters signs and screens)")
ap.add_argument("--source", default=os.environ.get("CAMERA_SOURCE", "demo-cam"))
ap.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
ap.add_argument("--api-key", default=os.environ.get("API_KEY"))
ap.add_argument("--spool", default=str(HERE / ".spool"))
ap.add_argument("--regex", default=r"^[A-Z0-9]{2,8}$")
a = ap.parse_args()
if not a.api_key:
    sys.exit("Set API_KEY (the camera key)")

sys.path.insert(0, str(a.backend / "bridge"))
sys.path.insert(0, str(HERE))
import cv2, numpy as np, serial
import freewili_bridge as fb
import apple_vision_ocr

sys.stdout.reconfigure(line_buffering=True)


class Gate:
    """Wraps FrameParser: only frames worth uploading come out of feed()."""

    def __init__(self, inner):
        self.inner = inner
        self.seen = {}               # plate -> last time saved
        self.last_button = -1e9
        self.got_frame = True        # no capture in flight
        self.frames = self.kept = 0

    @property
    def bad_crc(self):
        return self.inner.bad_crc

    def feed(self, data):
        out = []
        for jpg in self.inner.feed(data):
            self.got_frame = True
            self.frames += 1
            try:
                (Path(a.spool) / "last_frame.jpg").write_bytes(jpg)   # newest frame only, for debugging
            except OSError:
                pass
            if time.monotonic() - self.last_button < 6:      # photo from the green button: always keep
                self.kept += 1
                out.append(jpg)
                continue
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            plate = apple_vision_ocr.read_plate(img, a.regex, a.min_height)[0] if img is not None else None
            if plate and sum(ch.isdigit() for ch in plate) < 2:     # plates have 2+ digits; keyboard/signs rarely do
                plate = None
            now = time.monotonic()
            if not plate:
                print("auto: no plate", flush=True)
            elif now - self.seen.get(plate, -1e9) < a.repeat_seconds:
                self.seen[plate] = now                       # still in view: keep the timer going
                print(f"auto: {plate} still in view, skipped")
            else:
                self.seen[plate] = now
                self.kept += 1
                print(f"auto: plate {plate} -> saving")
                out.append(jpg)
        return out


class AutoBridge(fb.Bridge):
    def trigger(self, why):
        ok = super().trigger(why)
        if ok and why != "auto":
            self.gate.last_button = time.monotonic()
        return ok


spool = fb.Spool(Path(a.spool))
uploader = fb.Uploader(spool, a.api_url, a.api_key)
uploader.start()
if spool.pending():
    uploader.wake.set()

while True:
    esp_port, fw_port = fb.find_ports()
    if not esp_port:
        print("camera not found, retrying...", file=sys.stderr)
        time.sleep(2)
        continue
    try:
        esp = serial.Serial(esp_port, 115200, timeout=0.05)
        fw = serial.Serial(fw_port, 115200, timeout=0.05) if fw_port else None
    except serial.SerialException as e:
        print(f"cannot open port ({e}); retrying...", file=sys.stderr)
        time.sleep(2)
        continue
    print(f"AUTO MODE every {a.interval}s | camera: {esp_port} | FREE-WILi: {fw_port or 'not found'} | API: {a.api_url}")
    bridge = AutoBridge(esp, fw, spool, uploader, a.source[:32])
    gate = Gate(bridge.parser)
    gate.__dict__  # noqa
    bridge.gate = gate
    bridge.parser = gate
    next_auto = time.monotonic() + 1
    sent_at = 0.0
    try:
        while True:
            now = time.monotonic()
            # next automatic photo: only when the last one has come back (or 8 s passed)
            if now >= next_auto and (gate.got_frame or now - sent_at > 8):
                if bridge.trigger("auto"):
                    gate.got_frame = False
                    sent_at = now
                next_auto = now + a.interval
            bridge.step()
    except serial.SerialException:
        print("USB disconnected, reconnecting...", file=sys.stderr)
        for p in (esp, fw):
            try:
                p and p.close()
            except Exception:
                pass
        time.sleep(1)
    except KeyboardInterrupt:
        print(f"\nstopped. frames={gate.frames} kept={gate.kept}")
        break
