"""How Fahad's camera side (or anyone) calls the API.

Send a saved photo:
  python examples/send_image.py --file car.jpg

Grab one frame from a camera stream (RTSP URL from the camera's settings) and send it:
  python examples/send_image.py --rtsp "rtsp://user:pass@192.168.1.50:554/stream1"

Env: API_URL (default http://localhost:8000), API_KEY
"""
import argparse
import os
import sys

import requests  # pip install requests

API_URL = os.environ.get("API_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "")


def grab_frame(rtsp_url: str) -> bytes:
    import cv2
    cap = cv2.VideoCapture(rtsp_url)
    for _ in range(5):          # skip a few frames; first ones are often grey/blurry
        cap.read()
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit("Could not read from camera stream")
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--rtsp")
    args = ap.parse_args()

    data = open(args.file, "rb").read() if args.file else grab_frame(args.rtsp)
    r = requests.post(f"{API_URL}/recognize", headers={"X-API-Key": API_KEY},
                      files={"file": ("capture.jpg", data, "image/jpeg")},
                      params={"source": "garage-cam-1"}, timeout=30)
    print(r.status_code, r.json())


if __name__ == "__main__":
    main()
