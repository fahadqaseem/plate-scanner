#!/usr/bin/env python3
"""FREE-WILi + ESP32-P4-EYE  ->  LPR backend.

Runs on the laptop the two boards are plugged into (see Fahad's plate-scanner repo,
github.com/fahadqaseem/plate-scanner, for the firmware and wiring):

    FREE-WILi green button --USB "pulse"--> this bridge --USB "T"--> ESP32-P4-EYE
                                                 ^                        |
                                                 +--- PLATEJPG frame -----+
                                                 |
                                                 +--> POST /recognize (queued on disk, retried)

    pip install -r bridge/requirements.txt
    export API_URL=https://your-api.example.com API_KEY=<camera key>
    python bridge/freewili_bridge.py --source garage-cam-1
    python bridge/freewili_bridge.py --manual          # press Enter to capture (no FREE-WILi)

What it adds on top of Fahad's receive_plates.py (same wire format, same USB detection):
  * Uploads run on a background thread, so serial reading never stalls during OCR.
  * Every capture is written to a spool folder FIRST, then uploaded. If the API or
    Wi-Fi is down, images wait there and are sent when it comes back. Deleted
    after a successful upload (plates are personal data; nothing piles up).
  * Each capture gets a capture_id (UUID) and captured_at time. Retries reuse the
    capture_id, and the backend returns the existing record instead of a duplicate.
    Queued images keep their real capture time, not the upload time.
  * Reconnects if a USB cable is pulled or a board resets.
"""
import argparse
import json
import os
import queue
import struct
import sys
import threading
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Wire protocol (from fahadqaseem/plate-scanner host/receive_plates.py, unchanged)
#   b"PLATEJPG" | u32 length (LE) | JPEG | u32 CRC32 of JPEG (LE)
# ESP log text shares the port; anything that is not a valid frame is ignored.
# ---------------------------------------------------------------------------
MAGIC = b"PLATEJPG"
MAX_JPEG = 2_000_000
ESP_USB_VID = 0x303A              # Espressif
FREEWILI_PRODUCT = "FWOG main"    # FREE-WILi OG main CPU port
PULSE_MARKER = b"[platescan_main] pulse"
TRIGGER_BYTE = b"T"
TRIGGER_DEBOUNCE_S = 1.5          # firmware ignores triggers closer than this anyway


class FrameParser:
    """Feed raw bytes, get back complete, CRC-checked JPEGs."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                del self.buf[:-len(MAGIC)]          # keep a possible partial magic
                return out
            del self.buf[:i]
            if len(self.buf) < 12:
                return out
            n = struct.unpack_from("<I", self.buf, 8)[0]
            if n == 0 or n > MAX_JPEG:              # false magic: skip it
                del self.buf[:1]
                continue
            if len(self.buf) < 12 + n + 4:
                return out                          # wait for the rest
            jpg = bytes(self.buf[12:12 + n])
            crc = struct.unpack_from("<I", self.buf, 12 + n)[0]
            if zlib.crc32(jpg) == crc:
                out.append(jpg)
                del self.buf[:12 + n + 4]
            else:
                self.bad_crc += 1
                print("bad CRC, dropped a frame", file=sys.stderr)
                del self.buf[:1]


def encode_frame(jpg: bytes) -> bytes:
    """Inverse of FrameParser (used by tests and for replaying captures)."""
    return MAGIC + struct.pack("<I", len(jpg)) + jpg + struct.pack("<I", zlib.crc32(jpg))


# ---------------------------------------------------------------------------
# Spool: capture -> disk -> upload -> delete
# ---------------------------------------------------------------------------
class Spool:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.rejected = self.dir / "rejected"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rejected.mkdir(exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)               # plate images: owner-only
        except OSError:
            pass

    def add(self, jpg: bytes, source: str) -> str:
        capture_id = str(uuid.uuid4())
        meta = {"capture_id": capture_id, "source": source,
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")}
        # write image then metadata, each atomically; the .json marks the capture as complete
        self._atomic(self.dir / f"{capture_id}.jpg", jpg)
        self._atomic(self.dir / f"{capture_id}.json", json.dumps(meta).encode())
        return capture_id

    def pending(self) -> list[dict]:
        items = []
        for m in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                meta = json.loads(m.read_text())
            except (OSError, ValueError):
                continue
            if (self.dir / f"{meta['capture_id']}.jpg").exists():
                items.append(meta)
        return items

    def image(self, capture_id: str) -> bytes:
        return (self.dir / f"{capture_id}.jpg").read_bytes()

    def done(self, capture_id: str) -> None:
        for ext in ("jpg", "json"):
            (self.dir / f"{capture_id}.{ext}").unlink(missing_ok=True)

    def reject(self, capture_id: str, reason: str) -> None:
        """Server said the image itself is bad (4xx): keep it aside for a look, don't retry."""
        for ext in ("jpg", "json"):
            src = self.dir / f"{capture_id}.{ext}"
            if src.exists():
                src.replace(self.rejected / src.name)
        (self.rejected / f"{capture_id}.reason.txt").write_text(reason[:500])

    @staticmethod
    def _atomic(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Uploader (background thread)
# ---------------------------------------------------------------------------
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


def requests_post(url, headers, params, files, timeout):
    import requests
    r = requests.post(url, headers=headers, params=params, files=files, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {"detail": r.text[:300]}
    return r.status_code, body


class Uploader:
    def __init__(self, spool: Spool, api_url: str, api_key: str | None,
                 post_fn=requests_post, on_result=None, max_backoff: float = 60.0):
        self.spool = spool
        self.url = api_url.rstrip("/") + "/recognize"
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.post_fn = post_fn
        self.on_result = on_result or print_result
        self.max_backoff = max_backoff
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.backoff = 0.0

    def upload_one(self, meta: dict) -> str:
        """Returns 'done', 'rejected' or 'retry'."""
        cid = meta["capture_id"]
        params = {"source": meta["source"], "captured_at": meta["captured_at"], "capture_id": cid}
        files = {"file": (f"{cid}.jpg", self.spool.image(cid), "image/jpeg")}
        try:
            code, body = self.post_fn(self.url, self.headers, params, files, 30)
        except Exception as e:                      # network down, DNS, timeout...
            print(f"upload failed ({type(e).__name__}), will retry: {cid}", file=sys.stderr)
            return "retry"
        if 200 <= code < 300:
            self.spool.done(cid)
            self.on_result(meta, body)
            return "done"
        if code == 401:
            print("API key rejected (401). Check API_KEY; images stay queued.", file=sys.stderr)
            return "retry"
        if code in RETRYABLE:
            print(f"server busy/error {code}, will retry: {cid}", file=sys.stderr)
            return "retry"
        self.spool.reject(cid, f"{code} {body}")
        print(f"server rejected capture {cid}: {code} {body.get('detail')}", file=sys.stderr)
        return "rejected"

    def drain(self) -> bool:
        """Upload everything pending. False if something has to be retried later."""
        for meta in self.spool.pending():
            if self.stop.is_set():
                return False
            if self.upload_one(meta) == "retry":
                return False
        return True

    def run(self):
        while not self.stop.is_set():
            if self.drain():
                self.backoff = 0.0
                self.wake.wait()                    # sleep until a new capture arrives
            else:
                self.backoff = min(self.max_backoff, max(2.0, self.backoff * 2))
                self.wake.wait(self.backoff)
            self.wake.clear()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="uploader", daemon=True)
        t.start()
        return t


def print_result(meta: dict, body: dict) -> None:
    status = body.get("status")
    plate = body.get("plate_number") or "-"
    extra = ""
    if body.get("vehicle_match") is True:
        extra = f"  -> truck {body.get('vehicle_id')} ({body.get('match_type')})"
    elif body.get("vehicle_match") is False:
        extra = "  -> not a registered truck"
    print(f"[{meta['captured_at']}] {plate}  {status}  conf={body.get('confidence')}{extra}")


# ---------------------------------------------------------------------------
# Serial side
# ---------------------------------------------------------------------------
def find_ports():
    """(esp_port, freewili_main_port) by USB identity; either may be None."""
    from serial.tools import list_ports
    esp = fw = None
    for p in list_ports.comports():
        if p.vid == ESP_USB_VID:
            esp = p.device
        elif FREEWILI_PRODUCT in (p.product or ""):
            fw = p.device
    return esp, fw


class Bridge:
    """One step = read whatever the boards sent, trigger the camera on a button press,
    spool complete JPEGs. esp/fw are pyserial-like objects (read/write)."""

    def __init__(self, esp, fw, spool: Spool, uploader: Uploader | None, source: str,
                 keep_copies: Path | None = None, clock=time.monotonic):
        self.esp, self.fw = esp, fw
        self.spool, self.uploader, self.source = spool, uploader, source
        self.keep = keep_copies
        self.parser = FrameParser()
        self.fwbuf = b""
        self.clock = clock
        self.last_trigger = -1e9
        self.triggers = self.captures = 0

    def trigger(self, why: str) -> bool:
        now = self.clock()
        if now - self.last_trigger < TRIGGER_DEBOUNCE_S:
            return False
        self.last_trigger = now
        self.triggers += 1
        print(f"{why} -> capture")
        self.esp.write(TRIGGER_BYTE)
        return True

    def step(self) -> list[str]:
        if self.fw is not None:
            self.fwbuf += self.fw.read(4096)
            while b"\n" in self.fwbuf:
                line, self.fwbuf = self.fwbuf.split(b"\n", 1)
                if PULSE_MARKER in line:
                    self.trigger("FREE-WILi button")
            self.fwbuf = self.fwbuf[-4096:]         # no newline for a long time: don't grow forever
        ids = []
        for jpg in self.parser.feed(self.esp.read(65536)):
            cid = self.spool.add(jpg, self.source)
            self.captures += 1
            ids.append(cid)
            print(f"captured {len(jpg) // 1024} KB  id={cid[:8]}")
            if self.keep:
                self.keep.mkdir(parents=True, exist_ok=True)
                (self.keep / time.strftime("plate_%Y%m%d_%H%M%S.jpg")).write_bytes(jpg)
            if self.uploader:
                self.uploader.wake.set()
        return ids


def _stdin_triggers(q: queue.Queue):
    for _ in sys.stdin:
        q.put("Enter")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("API_KEY"),
                    help="camera API key (or set API_KEY); prefer the env var so it stays out of shell history")
    ap.add_argument("--source", default=os.environ.get("CAMERA_SOURCE", "garage-cam-1"),
                    help="name of this camera, stored with every record")
    ap.add_argument("--camera", help="ESP32-P4-EYE serial port (auto-detected)")
    ap.add_argument("--freewili", help="FREE-WILi main serial port (auto-detected)")
    ap.add_argument("--spool", default="spool", help="queue folder for not-yet-uploaded images")
    ap.add_argument("--manual", action="store_true", help="press Enter to trigger a capture")
    ap.add_argument("--keep-copies", type=Path,
                    help="also save every JPEG here (off by default: plates are personal data)")
    ap.add_argument("--no-upload", action="store_true", help="capture and queue only")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)     # show captures immediately, even when piped to a log

    if not a.api_key and not a.no_upload:
        sys.exit("Set API_KEY (the camera key) or pass --api-key")
    if a.api_url.startswith("http://") and "localhost" not in a.api_url and "127.0.0.1" not in a.api_url:
        print("WARNING: sending plate images over plain HTTP. Use https:// for anything but localhost.",
              file=sys.stderr)

    import serial

    spool = Spool(Path(a.spool))
    uploader = None
    if not a.no_upload:
        uploader = Uploader(spool, a.api_url, a.api_key)
        uploader.start()
        if spool.pending():
            print(f"{len(spool.pending())} queued capture(s) from earlier, uploading")
            uploader.wake.set()

    manual_q: queue.Queue = queue.Queue()
    if a.manual:
        threading.Thread(target=_stdin_triggers, args=(manual_q,), daemon=True).start()

    while True:                                     # reconnect loop
        esp_port, fw_port = find_ports()
        esp_port, fw_port = a.camera or esp_port, a.freewili or fw_port
        if not esp_port:
            print("ESP32-P4-EYE not found (plug in its Debug USB-C port). Retrying...", file=sys.stderr)
            time.sleep(2)
            continue
        try:
            esp = serial.Serial(esp_port, 115200, timeout=0.05)
            fw = serial.Serial(fw_port, 115200, timeout=0.05) if fw_port else None
        except serial.SerialException as e:
            print(f"cannot open port ({e}); is another program using it? Retrying...", file=sys.stderr)
            time.sleep(2)
            continue
        print(f"camera: {esp_port} | FREE-WILi: {fw_port or 'not found'}"
              f"{' | press Enter to capture' if a.manual else ''} | API: {a.api_url}")
        bridge = Bridge(esp, fw, spool, uploader, a.source[:32], a.keep_copies)
        try:
            while True:
                while not manual_q.empty():
                    manual_q.get()
                    bridge.trigger("manual")
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
            pending = len(spool.pending())
            print(f"\nstopped. {pending} capture(s) still queued in {spool.dir}/" if pending else "\nstopped.")
            return


if __name__ == "__main__":
    main()
