"""Shared OCR pieces: upload validation, text cleanup and the OcrResult type.

The OCR itself lives in:
  ocr_plate.py   plate crop -> text (character isolation + Tesseract)
  locate.py      full camera frame -> plate crops (used by recognize_plate_scene)
  ocr_rekognition.py  optional Amazon Rekognition engine
"""
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

PLATE_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class InvalidImageError(ValueError):
    pass


@dataclass
class OcrResult:
    plate: str | None
    confidence: float          # 0-100, mean Tesseract word confidence
    raw_text: str
    candidates_tried: int
    all_readings: list[tuple[str, float]] = field(default_factory=list)


def detect_format(data: bytes) -> str:
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    if data.startswith(PNG_MAGIC):
        return "png"
    raise InvalidImageError("Only JPEG or PNG images are accepted")


def decode_image(data: bytes, max_bytes: int) -> np.ndarray:
    if not data:
        raise InvalidImageError("Empty file")
    if len(data) > max_bytes:
        raise InvalidImageError(f"Image larger than {max_bytes // (1024 * 1024)} MB")
    detect_format(data)  # check magic bytes, not the client-supplied content type
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise InvalidImageError("File could not be decoded as an image")
    h, w = img.shape[:2]
    if w < 40 or h < 15:
        raise InvalidImageError("Image too small to read a plate")
    if w * h > 40_000_000:
        raise InvalidImageError("Image dimensions too large")
    return img


def clean_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())
