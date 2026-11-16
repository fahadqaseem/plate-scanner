"""Alternative OCR engine: Amazon Rekognition DetectText.

Switch with OCR_ENGINE=rekognition in .env. Same OcrResult as Tesseract,
so main.py and the API contract don't change.

Needs IAM permission: rekognition:DetectText. Costs per image (see README).
"""
import re

import boto3
import cv2

from .ocr import OcrResult, clean_text

_client = None


def recognize_plate_rekognition(img, plate_regex: str, region: str) -> OcrResult:
    global _client
    if _client is None:
        _client = boto3.client("rekognition", region_name=region)

    # Rekognition accepts up to 5 MB raw bytes; re-encode to keep it small
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    resp = _client.detect_text(Image={"Bytes": buf.tobytes()})

    pattern = re.compile(plate_regex)
    readings = []
    for det in resp.get("TextDetections", []):
        if det["Type"] != "LINE":
            continue
        text = clean_text(det["DetectedText"])
        if text:
            readings.append((text, float(det["Confidence"])))

    # Plates are usually the largest valid alphanumeric line; prefer longest valid, then confidence
    valid = [(t, c) for t, c in readings if pattern.match(t) and any(ch.isdigit() for ch in t)]
    if valid:
        best = max(valid, key=lambda tc: (len(tc[0]), tc[1]))
        return OcrResult(best[0], round(best[1], 1), best[0], 1, readings)
    raw = readings[0][0] if readings else ""
    return OcrResult(None, 0.0, raw, 1, readings)
