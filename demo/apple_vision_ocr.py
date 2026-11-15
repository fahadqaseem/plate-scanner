"""macOS-only plate reader using Apple's Vision text recognition (offline).
Used by run_local_demo.py in front of Anvita's Tesseract pipeline, which stays as the fallback.
Needs: pip install pyobjc-framework-Vision pyobjc-framework-Quartz
"""
import re
import cv2

_ALNUM = re.compile(r"[^A-Z0-9]")


def _lines(img_bgr):
    import Quartz, Vision
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        return []
    from Foundation import NSData
    data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)
    out = []
    for o in req.results() or []:
        cands = list(o.topCandidates_(5))
        # prefer the best reading made only of plate-like characters (drops junk symbols)
        clean = [c for c in cands if re.fullmatch(r"[A-Za-z0-9 \-\u2022.]+", c.string())]
        c = (clean or cands)[0]
        bb = o.boundingBox()
        out.append((c.string(), float(c.confidence()), float(bb.size.height),
                    (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height)))
    return out


def _best(lines, pattern, min_height=0.0):
    best = None
    for text, conf, height, box in lines:
        t = _ALNUM.sub("", text.upper())
        if height < min_height or not (4 <= len(t) <= 8) or not pattern.match(t):
            continue
        if not (any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t)):
            continue
        if best is None or height > best[2]:
            best = (t, conf * 100.0, height, box)
    return best


def read_plate(img_bgr, plate_regex, min_height=0.0):
    """Return (plate, confidence 0-100, all_lines) or (None, 0, lines). The plate is the
    largest text line that looks like a plate: 4-8 letters/digits with at least one of each.
    A second pass re-reads a tight crop of that line, which fixes clipped end characters."""
    pattern = re.compile(plate_regex)
    lines = _lines(img_bgr)
    best = _best(lines, pattern, min_height)
    if not best:
        return None, 0.0, lines
    H, W = img_bgr.shape[:2]
    x, y, w, h = best[3]
    x0, x1 = int(max(0, (x - 0.15 * w) * W)), int(min(W, (x + 1.15 * w) * W))
    y0, y1 = int(max(0, (1 - (y + h) - 0.4 * h) * H)), int(min(H, (1 - y + 0.4 * h) * H))
    if x1 - x0 > 20 and y1 - y0 > 10:
        crop = cv2.copyMakeBorder(img_bgr[y0:y1, x0:x1], 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        again = _best(_lines(crop), pattern)
        if again and again[0] != best[0] and len(again[0]) >= len(best[0]):
            best = again
    return best[0], best[1], lines
