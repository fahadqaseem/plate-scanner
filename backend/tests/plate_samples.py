"""Synthetic plate-only crops that look like real US plates (state name, slogan,
registration sticker, bolt holes, frame) for testing the OCR."""
import cv2
import numpy as np

FONTS = [cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_SIMPLEX]


def make_plate_crop(text="ABC1234", width=600, dark=False, blur=0, angle=0.0,
                    font=0, noise=6, frame=True, sticker=True, seed=0) -> np.ndarray:
    H, W = 300, 600                                   # draw at 2:1 like a 12"x6" plate
    bg, fg, small = ((40, 40, 40), (235, 235, 235), (200, 200, 200)) if dark else \
                    ((240, 240, 240), (25, 25, 120), (120, 40, 40))
    img = np.full((H, W, 3), bg, np.uint8)
    if frame:
        cv2.rectangle(img, (0, 0), (W - 1, H - 1), (15, 15, 15), 22)
    cv2.putText(img, "MICHIGAN", (205, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.1, small, 2, cv2.LINE_AA)
    cv2.putText(img, "GREAT LAKES STATE", (165, 262), cv2.FONT_HERSHEY_SIMPLEX, 0.9, small, 2, cv2.LINE_AA)
    for bx in (150, 450):                             # bolt holes
        cv2.circle(img, (bx, 40), 9, (90, 90, 90), -1)
    if sticker:
        cv2.rectangle(img, (480, 205), (560, 255), (30, 160, 230), -1)
        cv2.putText(img, "27", (497, 243), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)

    f = FONTS[font]
    scale = 3.2
    (tw, th), _ = cv2.getTextSize(text, f, scale, 10)
    if tw > 520:
        scale *= 520 / tw
        (tw, th), _ = cv2.getTextSize(text, f, scale, 10)
    cv2.putText(img, text, ((W - tw) // 2, 150 + th // 2), f, scale, fg, 10, cv2.LINE_AA)

    if angle:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
        img = cv2.warpAffine(img, m, (W, H), borderMode=cv2.BORDER_REPLICATE)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    img = cv2.resize(img, (width, width // 2), interpolation=cv2.INTER_AREA)
    if blur:
        img = cv2.GaussianBlur(img, (blur * 2 + 1, blur * 2 + 1), 0)
    return img


def to_jpeg(img: np.ndarray, quality=85) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def make_camera_frame(text="ABC1234", plate_width=300, cx=0.5, cy=0.62, seed=0,
                      clutter=True, brightness=1.0, plate_kw=None) -> np.ndarray:
    """A 1920x1080 frame like the ESP32-P4-EYE sends: a truck rear/front with the
    plate somewhere on it, plus clutter (grille bars, lights, signage text)."""
    rng = np.random.default_rng(seed)
    W, H = 1920, 1080
    img = np.zeros((H, W, 3), np.uint8)
    # garage background: vertical gradient + floor
    for y in range(H):
        v = int(70 + 60 * y / H)
        img[y] = (v, v, v + 5)
    cv2.rectangle(img, (0, int(H * 0.85)), (W, H), (55, 55, 60), -1)
    # truck body
    tx0, tx1 = int(W * 0.18), int(W * 0.82)
    cv2.rectangle(img, (tx0, int(H * 0.12)), (tx1, int(H * 0.88)), (150, 140, 135), -1)
    if clutter:
        for i in range(9):                                   # grille / ribs
            y = int(H * 0.2 + i * 38)
            cv2.rectangle(img, (tx0 + 60, y), (tx1 - 60, y + 14), (95, 90, 90), -1)
        cv2.rectangle(img, (tx0 + 20, int(H * 0.66)), (tx0 + 170, int(H * 0.74)), (30, 30, 200), -1)
        cv2.rectangle(img, (tx1 - 170, int(H * 0.66)), (tx1 - 20, int(H * 0.74)), (30, 30, 200), -1)
        cv2.putText(img, "LOGISTICS CO", (tx0 + 220, int(H * 0.16) + 40),
                    cv2.FONT_HERSHEY_DUPLEX, 2.0, (40, 40, 40), 4, cv2.LINE_AA)
        cv2.putText(img, "EXIT 3", (60, 110), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (230, 230, 230), 5)
        cv2.putText(img, "DOT 1234567", (tx1 - 520, int(H * 0.8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, (30, 30, 30), 3)
    plate = make_plate_crop(text, width=plate_width, seed=seed, **(plate_kw or {}))
    ph, pw = plate.shape[:2]
    x = int(W * cx - pw / 2); y = int(H * cy - ph / 2)
    cv2.rectangle(img, (x - 12, y - 12), (x + pw + 12, y + ph + 12), (60, 60, 60), -1)  # bumper mount
    img[y:y + ph, x:x + pw] = plate
    img = np.clip(img.astype(np.float32) * brightness + rng.normal(0, 4, img.shape), 0, 255)
    return img.astype(np.uint8)
