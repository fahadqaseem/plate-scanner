"""Find license plates in a full camera frame (e.g. 1920x1080 from the ESP32-P4-EYE)
and hand tight crops to the plate-crop OCR in ocr_plate.py.

Why not just OCR the whole frame: at 1920x1080 a plate is ~10-20% of the width,
and the frame also has signage, DOT numbers and grille lines. Tesseract reads
fragments of all of it. So we first find *where* the plate is:

  1. downscale frames wider than 1920 px (full HD is kept: small plates need the detail)
  2. find rows of character-shaped blobs (similar height, same baseline, close together),
     in both polarities (dark-on-light and light-on-dark plates)
  3. around each row, look for the plate rectangle (2:1-ish box that encloses the row).
     A row with a rectangle around it is very likely a plate; a row of text painted
     on the truck body (company name, DOT number) usually has none.
  4. return full-resolution crops, plate-like ones first

Optional ROI: a fixed garage camera always sees the plate in roughly the same spot.
Setting PLATE_ROI crops to that area first, which removes most distractors and is
the single most effective accuracy setting for a fixed camera.
"""
from dataclasses import dataclass

import cv2
import numpy as np

DETECT_WIDTH = 1920   # full HD: small plates lose their characters if we downscale


@dataclass
class PlateCandidate:
    crop: np.ndarray          # BGR, full resolution
    box: tuple[int, int, int, int]   # x0, y0, x1, y1 in the original frame
    n_chars: int              # characters in the row that produced it
    has_rect: bool            # a plate-shaped rectangle encloses the row


def parse_roi(roi: str | None) -> tuple[float, float, float, float] | None:
    """'x0,y0,x1,y1' as fractions of the frame (0-1). None/'' = whole frame."""
    if not roi:
        return None
    parts = [float(p) for p in roi.split(",")]
    if len(parts) != 4:
        raise ValueError("PLATE_ROI must be 'x0,y0,x1,y1' fractions, e.g. 0.25,0.4,0.75,0.95")
    x0, y0, x1, y1 = parts
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("PLATE_ROI values must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    return x0, y0, x1, y1


def apply_roi(img: np.ndarray, roi) -> tuple[np.ndarray, int, int]:
    if roi is None:
        return img, 0, 0
    H, W = img.shape[:2]
    x0, y0 = int(roi[0] * W), int(roi[1] * H)
    x1, y1 = int(roi[2] * W), int(roi[3] * H)
    return img[y0:y1, x0:x1], x0, y0


def _char_rows(binary: np.ndarray) -> list[list[tuple]]:
    """Groups of character-like blobs on one line. Blob = (x, y, w, h)."""
    H, W = binary.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (10 <= h <= 0.3 * H) or w < 2:
            continue
        if not (0.45 <= h / w <= 8.0):           # wide M/W down to a thin "1"
            continue
        fill = area / float(w * h)
        if not (0.15 <= fill <= 0.92):
            continue
        blobs.append((x, y, w, h))
    if len(blobs) < 3:
        return []
    blobs.sort(key=lambda b: b[0])

    # union-find: link blobs of similar height on the same line with small gaps
    parent = list(range(len(blobs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, (xi, yi, wi, hi) in enumerate(blobs):
        for j in range(i + 1, len(blobs)):
            xj, yj, wj, hj = blobs[j]
            gap = xj - (xi + wi)
            if gap > 1.3 * max(hi, hj):
                break                              # sorted by x: nothing further can link
            if abs(hi - hj) > 0.25 * max(hi, hj):
                continue
            if abs((yi + hi / 2) - (yj + hj / 2)) > 0.3 * max(hi, hj):
                continue
            parent[find(i)] = find(j)

    groups: dict[int, list] = {}
    for i, b in enumerate(blobs):
        groups.setdefault(find(i), []).append(b)
    # plates: 3-8 characters, plus slack for a split/broken character
    return [g for g in groups.values() if 3 <= len(g) <= 10]


def _enclosing_rect(edges: np.ndarray, row_box) -> tuple[int, int, int, int] | None:
    """Smallest plate-shaped rectangle (axis-aligned bbox of a contour) around the row."""
    rx0, ry0, rx1, ry1 = row_box
    rw, rh = rx1 - rx0, ry1 - ry0
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (x <= rx0 and y <= ry0 and x + w >= rx1 and y + h >= ry1):
            continue
        if not (1.4 <= w / max(h, 1) <= 3.6):
            continue
        if not (1.15 * rw <= w <= 2.2 * rw and 1.6 * rh <= h <= 5.0 * rh):
            continue
        if best is None or w * h < best[2] * best[3]:
            best = (x, y, w, h)
    if best is None:
        return None
    x, y, w, h = best
    return x, y, x + w, y + h


def locate_plates(img: np.ndarray, max_candidates: int = 6) -> list[PlateCandidate]:
    H0, W0 = img.shape[:2]
    scale = min(1.0, DETECT_WIDTH / W0)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    H, W = gray.shape

    dark_text = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY_INV, 31, 15)
    light_text = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 31, -15)
    edges = cv2.dilate(cv2.Canny(blur, 40, 140), np.ones((3, 3), np.uint8))

    found = []
    for binary in (dark_text, light_text):
        for row in _char_rows(binary):
            x0 = min(b[0] for b in row); x1 = max(b[0] + b[2] for b in row)
            y0 = min(b[1] for b in row); y1 = max(b[1] + b[3] for b in row)
            h = float(np.median([b[3] for b in row]))
            if (x1 - x0) / h < 1.5:                 # too short to be a registration number
                continue
            pad = int(3 * h)
            ex0, ey0 = max(0, x0 - pad), max(0, y0 - pad)
            ex1, ey1 = min(W, x1 + pad), min(H, y1 + pad)
            rect = _enclosing_rect(edges[ey0:ey1, ex0:ex1], (x0 - ex0, y0 - ey0, x1 - ex0, y1 - ey0))
            if rect:
                bx0, by0, bx1, by1 = rect[0] + ex0, rect[1] + ey0, rect[2] + ex0, rect[3] + ey0
                # small margin so the frame/border is inside the crop (ocr_plate handles it)
                m = int(0.04 * (bx1 - bx0))
                box = (bx0 - m, by0 - m, bx1 + m, by1 + m)
            else:
                # no rectangle: guess the plate box from the row (chars ~45% of plate height)
                box = (x0 - 0.5 * h, y0 - 0.75 * h, x1 + 0.5 * h, y1 + 0.75 * h)
            found.append((box, len(row), rect is not None))

    # dedupe overlapping boxes (both polarities often find the same plate)
    found.sort(key=lambda f: (f[2], -abs(f[1] - 7)), reverse=True)
    kept: list[tuple] = []
    for box, n, has_rect in found:
        if any(_iou(box, k[0]) > 0.4 for k in kept):
            continue
        kept.append((box, n, has_rect))
        if len(kept) >= max_candidates:
            break

    out = []
    for (bx0, by0, bx1, by1), n, has_rect in kept:
        X0, Y0 = max(0, int(bx0 / scale)), max(0, int(by0 / scale))
        X1, Y1 = min(W0, int(bx1 / scale)), min(H0, int(by1 / scale))
        if X1 - X0 < 40 or Y1 - Y0 < 15:
            continue
        out.append(PlateCandidate(img[Y0:Y1, X0:X1], (X0, Y0, X1, Y1), n, has_rect))
    return out


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0
