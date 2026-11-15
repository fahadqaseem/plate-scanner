#!/usr/bin/env python3
"""Receive plate JPEGs from the ESP32-P4-EYE over its USB serial cable.

    pip install pyserial requests
    python receive_plates.py                       # auto-detects both boards, saves to ./plates
    python receive_plates.py --post http://localhost:8000/recognize --api-key KEY

Frame on the wire:  b"PLATEJPG" | u32 length (LE) | JPEG | u32 CRC32 of JPEG (LE)
Log text from the ESP shares the port; anything that is not a valid frame is ignored.
"""
import argparse, struct, sys, time, zlib
from pathlib import Path

MAGIC = b"PLATEJPG"
MAX_JPEG = 2_000_000


class FrameParser:
    """Feed raw bytes, get back complete, CRC-checked JPEGs."""
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
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
                print("bad CRC, dropped a frame", file=sys.stderr)
                del self.buf[:1]


def post(url, key, jpg, name):
    import requests
    headers = {"X-API-Key": key} if key else {}
    r = requests.post(url, headers=headers, files={"file": (name, jpg, "image/jpeg")}, timeout=30)
    print(r.status_code, r.text[:300])


def find_ports():
    """(esp_port, freewili_main_port) by USB identity; either may be None."""
    from serial.tools import list_ports
    esp = fw = None
    for p in list_ports.comports():
        if p.vid == 0x303A:
            esp = p.device
        elif "FWOG main" in (p.product or ""):
            fw = p.device
    return esp, fw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="ESP32-P4-EYE port (auto-detected if omitted)")
    ap.add_argument("--freewili", help="FREE-WILi main port; its button press triggers the camera (auto if omitted)")
    ap.add_argument("--out", default="plates")
    ap.add_argument("--post")
    ap.add_argument("--api-key")
    a = ap.parse_args()
    import serial
    esp_port, fw_port = find_ports()
    esp_port = a.port or esp_port
    fw_port = a.freewili or fw_port
    if not esp_port:
        sys.exit("ESP32-P4-EYE not found: plug its Debug USB port in")
    Path(a.out).mkdir(exist_ok=True)
    esp = serial.Serial(esp_port, 115200, timeout=0.05)
    fw = serial.Serial(fw_port, 115200, timeout=0.05) if fw_port else None
    print("camera:", esp_port, "| FREE-WILi:", fw_port or "not found (use Boot button / GPIO)")
    p, fwbuf = FrameParser(), b""
    while True:
        if fw:
            fwbuf += fw.read(4096)
            while b"\n" in fwbuf:
                line, fwbuf = fwbuf.split(b"\n", 1)
                if b"[platescan_main] pulse" in line:
                    print("FREE-WILi button -> capture")
                    esp.write(b"T")
        for jpg in p.feed(esp.read(65536)):
            name = time.strftime("plate_%Y%m%d_%H%M%S.jpg")
            Path(a.out, name).write_bytes(jpg)
            print("saved", name, len(jpg), "bytes")
            if a.post:
                post(a.post, a.api_key, jpg, name)


if __name__ == "__main__":
    main()
