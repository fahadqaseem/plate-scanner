"""OCR for images that are ALREADY cropped to the license plate.

A plate crop contains more than the registration number: state name on top,
a slogan/county on the bottom, registration stickers, bolts, the frame and
sometimes a logo in the middle. Tesseract will happily read all of that, so
the job here is to isolate the big registration characters first.

Pipeline:
  1. upscale small crops (Tesseract needs characters ~30+ px tall)
  2. binarize both ways (dark-on-light and light-on-dark plates)
  3. connected components -> keep only blobs shaped like big characters
  4. pick the main row of similar-height blobs = the registration number
  5. redraw ONLY those blobs on a clean white canvas -> Tesseract single line
  6. fall back to the whole crop if character isolation fails
  7. choose the best reading (valid format, length matches blob count, confidence)
"""
import re

import cv2
import numpy as np
import pytesseract

from .ocr import PLATE_WHITELIST, OcrResult, clean_text

TARGET_HEIGHT = 220  # px; plates get upscaled/downscaled to this


def _prepare(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    scale = TARGET_HEIGHT / gray.shape[0]
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)
    # even out lighting (glare, shadows from the frame)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _binaries(gray: np.ndarray) -> list[np.ndarray]:
    """Text = white (255) on black, for both plate polarities and two thresholds."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu_inv = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adapt_inv = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY_INV, 41, 12)
    return [otsu_inv, adapt_inv, cv2.bitwise_not(otsu_inv)]  # last: light text on dark plate


def _character_blobs(binary: np.ndarray):
    """Return (labels, blobs) for the big registration characters.

    Each blob is (label, x, y, w, h, n_chars). n_chars > 1 means characters
    that touch each other (common with bold fonts or blur) and need splitting.
    """
    H, W = binary.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    cands = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (0.17 * H <= h <= 0.9 * H):       # state name / slogan are much shorter (~10%)
            continue
        if w > 0.45 * W or w < 2:                # frame edges / bars are too wide
            continue
        if not (0.3 <= h / w <= 8.0):           # chars, or 2-3 touching chars; "1" is thin
            continue
        fill = area / float(w * h)
        if not (0.12 <= fill <= 0.95):          # solid blocks (stickers) or noise
            continue
        if x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1:   # touching the edge = frame
            continue
        cands.append((i, x, y, w, h))
    if len(cands) < 2:
        return labels, []

    # keep the largest group of blobs with similar height on the same row
    best = []
    for _, _, y0, _, h0 in cands:
        cy0 = y0 + h0 / 2
        group = [c for c in cands
                 if abs(c[4] - h0) <= 0.25 * h0 and abs((c[2] + c[4] / 2) - cy0) <= 0.3 * h0]
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return labels, []

    # typical single-character width = median of the narrower-than-tall blobs
    singles = [c[3] for c in best if c[4] / c[3] >= 0.8] or [c[3] for c in best]
    char_w = float(np.median(singles))
    out = []
    for c in sorted(best, key=lambda c: c[1]):
        k = max(1, int(round(c[3] / (char_w * 1.05)))) if c[3] > 1.5 * char_w else 1
        out.append((*c, k))
    return labels, out


def _split_columns(blob_mask: np.ndarray, k: int) -> list[int]:
    """Columns where a k-character blob should be cut: thinnest column near each 1/k mark."""
    proj = blob_mask.sum(axis=0)
    w = len(proj)
    cuts = []
    for j in range(1, k):
        lo, hi = int(w * (j / k - 0.15)), int(w * (j / k + 0.15))
        cuts.append(lo + int(np.argmin(proj[lo:hi])))
    return cuts


def _estimate_tilt(gray: np.ndarray) -> float:
    """Angle (degrees) of the character row, from a line through the blob bottoms."""
    for binary in _binaries(gray):
        _, blobs = _character_blobs(binary)
        if len(blobs) >= 3:
            xs = np.array([b[1] + b[3] / 2 for b in blobs], float)
            ys = np.array([b[2] + b[4] for b in blobs], float)
            slope = np.polyfit(xs, ys, 1)[0]
            return float(np.degrees(np.arctan(slope)))
    return 0.0


def _deskew(gray: np.ndarray) -> np.ndarray:
    angle = _estimate_tilt(gray)
    if abs(angle) < 1.0 or abs(angle) > 20:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _render_clean(labels: np.ndarray, blobs) -> np.ndarray | None:
    """Draw only the chosen character blobs, black on white, with margins."""
    if len(blobs) < 2:
        return None
    x0 = min(b[1] for b in blobs); x1 = max(b[1] + b[3] for b in blobs)
    y0 = min(b[2] for b in blobs); y1 = max(b[2] + b[4] for b in blobs)
    mask = np.isin(labels[y0:y1, x0:x1], [b[0] for b in blobs])
    canvas = np.full(mask.shape, 255, np.uint8)
    canvas[mask] = 0
    # separate touching characters with a thin white cut
    gap = max(2, (y1 - y0) // 25)
    for lab, bx, by, bw, bh, k in blobs:
        if k > 1:
            sub = labels[by:by + bh, bx:bx + bw] == lab
            for cx in _split_columns(sub, k):
                col = bx - x0 + cx
                canvas[:, max(0, col - gap // 2): col + gap // 2 + 1] = 255
    # scale so characters are ~80 px tall, add white border
    canvas = cv2.resize(canvas, None, fx=80 / canvas.shape[0], fy=80 / canvas.shape[0],
                        interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(canvas, 20, 20, 25, 25, cv2.BORDER_CONSTANT, value=255)


def _ocr_line(img: np.ndarray) -> tuple[str, float]:
    cfg = f"--oem 3 --psm 7 -c tessedit_char_whitelist={PLATE_WHITELIST}"
    d = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for t, c in zip(d["text"], d["conf"]):
        c = float(c)
        if t.strip() and c >= 0:
            words.append(t)
            confs.append(c)
    return clean_text("".join(words)), (sum(confs) / len(confs) if confs else 0.0)


_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1"}


def fix_digit_context(text: str) -> str:
    """Inside a run of digits, O/Q/D -> 0 and I/L -> 1 (e.g. TRK9OO1 -> TRK9001).

    Only changes a letter run when BOTH sides are digits, so ABC-style letter
    groups are never touched.
    """
    chars = list(text)
    i = 0
    while i < len(chars):
        if chars[i] in _TO_DIGIT:
            j = i
            while j < len(chars) and chars[j] in _TO_DIGIT:
                j += 1
            if i > 0 and j < len(chars) and chars[i - 1].isdigit() and chars[j].isdigit():
                for t in range(i, j):
                    chars[t] = _TO_DIGIT[chars[t]]
            i = j
        else:
            i += 1
    return "".join(chars)


_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
_TO_DIGIT_ALL = {**_TO_DIGIT, "S": "5", "B": "8", "Z": "2", "G": "6"}


def apply_plate_formats(text: str, formats: list[str]) -> str:
    """Coerce look-alike characters by position for known plate formats.

    formats: patterns of L (letter), D (digit), * (either), e.g. ["LLLDDDD"] for
    standard Michigan plates. Among formats with the same length as the reading,
    picks the one needing the fewest changes, and only if every mismatched
    position has a look-alike (TRK9OOL -> TRK9001; ABC1234 untouched). Readings
    that fit no format are returned unchanged, so vanity/out-of-state plates pass.
    """
    best = None
    for fmt in formats:
        if len(fmt) != len(text):
            continue
        out, changes = [], 0
        for ch, kind in zip(text, fmt.upper()):
            if kind == "L" and ch.isdigit():
                if ch not in _TO_LETTER:
                    break
                out.append(_TO_LETTER[ch]); changes += 1
            elif kind == "D" and ch.isalpha():
                if ch not in _TO_DIGIT_ALL:
                    break
                out.append(_TO_DIGIT_ALL[ch]); changes += 1
            else:
                out.append(ch)
        else:
            if best is None or changes < best[1]:
                best = ("".join(out), changes)
    return best[0] if best else text


def recognize_plate_crop(img: np.ndarray, plate_regex: str,
                         tesseract_cmd: str | None = None,
                         digit_context_fix: bool = True,
                         plate_formats: list[str] | None = None) -> OcrResult:
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    pattern = re.compile(plate_regex)
    gray = _deskew(_prepare(img))

    readings: list[tuple[str, float, int]] = []   # text, confidence, blob count (0 = fallback)
    tried = 0

    # 1) isolated characters (main path)
    for binary in _binaries(gray):
        labels, blobs = _character_blobs(binary)
        clean = _render_clean(labels, blobs)
        if clean is None:
            continue
        tried += 1
        text, conf = _ocr_line(clean)
        if text:
            readings.append((text, conf, sum(b[5] for b in blobs)))

    # 2) fallback: whole crop, middle band (skips state name/slogan rows)
    if not any(pattern.match(t) and c >= 80 for t, c, _ in readings):
        H = gray.shape[0]
        for region in (gray, gray[int(0.18 * H): int(0.85 * H)]):
            for binary in _binaries(region)[:2]:
                tried += 1
                text, conf = _ocr_line(cv2.copyMakeBorder(cv2.bitwise_not(binary), 15, 15, 15, 15,
                                                          cv2.BORDER_CONSTANT, value=255))
                if text:
                    readings.append((text, conf * 0.9, 0))  # slight penalty: noisier source

    if digit_context_fix:
        readings = [(fix_digit_context(t), c, n) for t, c, n in readings]
    if plate_formats:
        readings = [(apply_plate_formats(t, plate_formats), c, n) for t, c, n in readings]

    def score(r):
        text, conf, nblobs = r
        votes = sum(1 for t, _, _ in readings if t == text)
        length_ok = 1 if nblobs and len(text) == nblobs else 0
        return (bool(pattern.match(text)), length_ok, votes, conf)

    simple = [(t, c) for t, c, _ in readings]
    if readings:
        best = max(readings, key=score)
        if pattern.match(best[0]):
            return OcrResult(best[0], round(best[1], 1), best[0], tried, simple)
        return OcrResult(None, 0.0, best[0], tried, simple)
    return OcrResult(None, 0.0, "", tried, simple)


NO_RECT_PENALTY = 0.7


def _rank(text: str, conf: float, has_rect: bool, pattern) -> tuple:
    valid = bool(pattern.match(text))
    mixed = any(c.isdigit() for c in text) and any(c.isalpha() for c in text)
    return (valid, has_rect, mixed, 5 <= len(text) <= 8, conf)


def recognize_plate_scene(img: np.ndarray, plate_regex: str,
                          tesseract_cmd: str | None = None,
                          digit_context_fix: bool = True,
                          roi=None, crop_fallback: bool = True,
                          plate_formats: list[str] | None = None) -> OcrResult:
    """Full camera frame -> plate text. Locates plate candidates, OCRs each crop
    with recognize_plate_crop, keeps the most plate-like valid reading.

    crop_fallback: if nothing is found, treat the whole image (or ROI) as a crop.
    That makes this safe for already-cropped images too (INPUT_MODE=auto).
    """
    from .locate import apply_roi, locate_plates

    pattern = re.compile(plate_regex)
    region, _, _ = apply_roi(img, roi)
    best, best_rank, tried, readings = None, None, 0, []
    for cand in locate_plates(region):
        r = recognize_plate_crop(cand.crop, plate_regex, tesseract_cmd, digit_context_fix, plate_formats)
        tried += r.candidates_tried
        readings += r.all_readings
        text = r.plate or r.raw_text
        if not text:
            continue
        # text with no plate rectangle around it (signage, DOT numbers) is less
        # trustworthy: lower its confidence so it tends to land in low_confidence
        conf = r.confidence if cand.has_rect else r.confidence * NO_RECT_PENALTY
        rank = _rank(text, conf if r.plate else 0.0, cand.has_rect, pattern)
        if best_rank is None or rank > best_rank:
            best, best_rank = (r.plate, conf, text), rank
        if r.plate and cand.has_rect and r.confidence >= 85:
            break                                    # confident read on a real plate box

    if (best is None or best[0] is None) and crop_fallback:
        r = recognize_plate_crop(region, plate_regex, tesseract_cmd, digit_context_fix, plate_formats)
        tried += r.candidates_tried
        readings += r.all_readings
        if r.plate:
            best = (r.plate, r.confidence, r.plate)

    if best and best[0]:
        return OcrResult(best[0], round(best[1], 1), best[2], tried, readings)
    return OcrResult(None, 0.0, best[2] if best else "", tried, readings)
