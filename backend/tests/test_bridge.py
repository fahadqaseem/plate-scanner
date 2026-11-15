"""FREE-WILi bridge + full-frame camera images, end to end.

Real OCR, AWS faked with moto (via the `client` fixture), serial ports faked.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

import freewili_bridge as fb  # noqa: E402
from plate_samples import make_camera_frame, to_jpeg  # noqa: E402
from test_api import API_KEY, REGION, client, post  # noqa: E402,F401  (client is a fixture)


class FakeSerial:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.written = b""

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, data):
        self.written += data


def api_post_fn(c, fail_first_response=False):
    """post_fn for the Uploader that goes to the FastAPI TestClient instead of the network."""
    state = {"calls": 0}

    def post_fn(url, headers, params, files, timeout):
        state["calls"] += 1
        r = c.post("/recognize", headers=headers, params=params, files=files)
        if fail_first_response and state["calls"] == 1:
            raise ConnectionError("response lost")     # server stored it, client never heard back
        return r.status_code, r.json()
    return post_fn, state


def camera_jpeg(text="ABC1234", **kw) -> bytes:
    return to_jpeg(make_camera_frame(text, **kw), quality=80)   # what the P4-EYE sends


# ---------- wire protocol ----------

def test_frame_parser_handles_log_noise_split_and_corrupt_frames():
    good1, good2 = b"\xff\xd8\xff" + b"a" * 500, b"\xff\xd8\xff" + b"b" * 300
    corrupt = bytearray(fb.encode_frame(b"\xff\xd8\xff" + b"c" * 200))
    corrupt[20] ^= 0xFF
    false_magic = fb.MAGIC + b"\xff\xff\xff\xff"      # absurd length
    stream = (b"I (123) cam: ready\n" + false_magic + fb.encode_frame(good1) + b"log line\n"
              + bytes(corrupt) + fb.encode_frame(good2))
    p = fb.FrameParser()
    out = []
    for i in range(0, len(stream), 37):              # arrives in small, odd-sized chunks
        out += p.feed(stream[i:i + 37])
    assert out == [good1, good2]
    assert p.bad_crc >= 1


def test_button_press_triggers_camera_once_per_debounce(tmp_path):
    t = [0.0]
    esp = FakeSerial()
    fw = FakeSerial([b"boot\n[platescan_main] pul", b"se 1\n", b"[platescan_main] pulse 2\n"])
    br = fb.Bridge(esp, fw, fb.Spool(tmp_path), None, "cam", clock=lambda: t[0])
    br.step(); br.step()
    assert esp.written == b"T"                        # line split across reads still counts
    t[0] = 0.5
    br.step()
    assert esp.written == b"T"                        # second press 0.5 s later is ignored
    fw.chunks.append(b"[platescan_main] pulse 3\n")
    t[0] = 2.0
    br.step()
    assert esp.written == b"TT"


# ---------- camera frame -> bridge -> API -> S3/DynamoDB ----------

def test_camera_frame_end_to_end(client, tmp_path):
    c, s3 = client
    spool = fb.Spool(tmp_path / "spool")
    post_fn, _ = api_post_fn(c)
    results = []
    up = fb.Uploader(spool, "http://test", API_KEY, post_fn=post_fn,
                     on_result=lambda m, b: results.append((m, b)))
    frame = fb.encode_frame(camera_jpeg("ABC1234"))
    esp = FakeSerial([b"I (5) plate: capture\n" + frame[:100000], frame[100000:]])
    br = fb.Bridge(esp, None, spool, up, "garage-cam-1")
    br.step(); br.step()
    assert len(spool.pending()) == 1                  # written to disk before upload

    assert up.drain()
    meta, body = results[0]
    assert body["plate_number"] == "ABC1234" and body["status"] == "success"
    assert body["vehicle_match"] is True and body["vehicle_id"] == "T01"
    assert body["record_id"] == meta["capture_id"]
    def t(v): return datetime.fromisoformat(v.replace("Z", "+00:00"))
    assert t(body["timestamp"]) == t(meta["captured_at"])   # capture time, not upload time
    assert spool.pending() == [] and not list((tmp_path / "spool").glob("*.jpg"))
    rec = c.get(f"/records/{body['record_id']}", headers={"X-API-Key": API_KEY}).json()
    assert rec["source"] == "garage-cam-1" and rec["matched_plate"] == "ABC1234"


def test_lost_response_retry_does_not_duplicate(client, tmp_path):
    c, s3 = client
    spool = fb.Spool(tmp_path)
    post_fn, state = api_post_fn(c, fail_first_response=True)
    results = []
    up = fb.Uploader(spool, "http://test", API_KEY, post_fn=post_fn,
                     on_result=lambda m, b: results.append(b))
    cid = spool.add(camera_jpeg("XYZ5678"), "cam")
    assert up.drain() is False                        # first try: response lost, still queued
    assert len(spool.pending()) == 1
    assert up.drain() is True                         # retry
    assert state["calls"] == 2
    assert results[0]["record_id"] == cid and results[0]["plate_number"] == "XYZ5678"
    objects = s3.list_objects_v2(Bucket="lpr-test-bucket").get("KeyCount", 0)
    assert objects == 1                               # stored once
    items = boto3.resource("dynamodb", region_name=REGION).Table("lpr-records").scan()["Items"]
    assert len(items) == 1


def test_api_down_keeps_queue_and_bad_image_is_set_aside(tmp_path):
    spool = fb.Spool(tmp_path)

    def down(*a, **k):
        raise ConnectionError("no route to host")
    up = fb.Uploader(spool, "http://test", API_KEY, post_fn=down)
    spool.add(b"\xff\xd8\xffjunk", "cam")
    assert up.drain() is False and len(spool.pending()) == 1

    for code in (502, 429, 401):                      # retryable: stays queued
        up.post_fn = lambda *a, code=code, **k: (code, {"detail": "x"})
        assert up.drain() is False and len(spool.pending()) == 1

    up.post_fn = lambda *a, **k: (400, {"detail": "File could not be decoded as an image"})
    assert up.drain() is True
    assert spool.pending() == []
    assert len(list((tmp_path / "rejected").glob("*.jpg"))) == 1


# ---------- API parameters the bridge uses ----------

def test_captured_at_and_capture_id_validation(client):
    c, _ = client
    img = camera_jpeg()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    for params in ({"captured_at": future}, {"captured_at": "2026-11-14T10:00:00"},
                   {"captured_at": "yesterday"}, {"capture_id": "001"}):
        r = c.post("/recognize", headers={"X-API-Key": API_KEY}, params=params,
                   files={"file": ("a.jpg", img, "image/jpeg")})
        assert r.status_code == 400, params


# ---------- full 1920x1080 frames in the default (auto) mode ----------

@pytest.mark.parametrize("kw", [dict(plate_width=400), dict(plate_width=220),
                                dict(plate_width=280, cx=0.3, cy=0.7), dict(brightness=0.55),
                                dict(plate_kw=dict(dark=True))])
def test_full_hd_camera_frames(client, kw):
    c, _ = client
    body = post(c, camera_jpeg("HMW4471", **kw)).json()
    assert body["plate_number"] == "HMW4471", body
    # dark (white-on-black) plates can read right but score below MIN_OCR_CONFIDENCE
    assert body["status"] == ("success" if "plate_kw" not in kw else body["status"])


def test_frame_without_plate_is_never_a_confident_match(client):
    import cv2
    c, _ = client
    f = make_camera_frame("ABC1234")
    cv2.rectangle(f, (760, 560), (1160, 800), (150, 140, 135), -1)   # no plate in view
    body = post(c, to_jpeg(f)).json()
    assert body["status"] in ("no_plate_found", "low_confidence")   # signage must not be a success
    assert body["vehicle_match"] is None


def test_roi_and_plate_formats(client, monkeypatch):
    from app.config import get_settings
    from app.ocr_plate import apply_plate_formats
    assert apply_plate_formats("TRK9OOL", ["LLLDDDD"]) == "TRK9001"
    assert apply_plate_formats("A8C1234", ["LLLDDDD"]) == "ABC1234"
    assert apply_plate_formats("DL4CAF5", ["LLLDDDD"]) == "DL4CAF5"   # other format: untouched
    assert apply_plate_formats("ABC1234", ["LLLDDDD", "DLLDDD"]) == "ABC1234"

    monkeypatch.setenv("PLATE_ROI", "0.25,0.4,0.75,0.95")
    monkeypatch.setenv("PLATE_FORMATS", "LLLDDDD")
    get_settings.cache_clear()
    c, _ = client
    body = post(c, camera_jpeg("GHT8834")).json()
    assert body["plate_number"] == "GHT8834"

    monkeypatch.setenv("PLATE_ROI", "0.9,0.1,0.2,0.5")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()
