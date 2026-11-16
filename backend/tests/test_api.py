"""End-to-end tests: real OpenCV + Tesseract, AWS faked with moto.

Run:  pytest -q
"""
import os
import sys

import boto3
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

sys.path.insert(0, os.path.dirname(__file__))

API_KEY = "test-key-camera-0123456789"
REGION = "us-east-2"


from plate_samples import make_plate_crop, to_jpeg


def make_plate_image(text="ABC1234", angle=0.0, **kw) -> bytes:
    """Plate-only crop (what the camera sends by default)."""
    return to_jpeg(make_plate_crop(text, angle=angle, **kw))


def make_scene_image(text="ABC1234") -> bytes:
    """Whole scene with a small plate in it (INPUT_MODE=full)."""
    img = np.full((480, 640, 3), 90, np.uint8)
    cv2.rectangle(img, (60, 80), (580, 420), (60, 60, 70), -1)
    cv2.rectangle(img, (200, 280), (440, 400), (245, 245, 245), -1)
    cv2.rectangle(img, (200, 280), (440, 400), (20, 20, 20), 4)
    cv2.putText(img, text, (212, 365), cv2.FONT_HERSHEY_SIMPLEX, 1.55, (15, 15, 15), 5, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


@pytest.fixture
def client(monkeypatch):
    env = {
        "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION,
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
        "S3_BUCKET": "lpr-test-bucket", "DYNAMODB_TABLE": "lpr-records",
        "VEHICLES_TABLE": "lpr-vehicles", "API_KEYS": f"{API_KEY},other-key-for-frontend",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="lpr-test-bucket",
                         CreateBucketConfiguration={"LocationConstraint": REGION})
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName="lpr-records", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "record_id", "AttributeType": "S"},
                                  {"AttributeName": "plate_number", "AttributeType": "S"},
                                  {"AttributeName": "timestamp", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "record_id", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "plate_number-index",
                "KeySchema": [{"AttributeName": "plate_number", "KeyType": "HASH"},
                              {"AttributeName": "timestamp", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"}}])
        ddb.create_table(
            TableName="lpr-vehicles", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "plate_number", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "plate_number", "KeyType": "HASH"}])
        boto3.resource("dynamodb", region_name=REGION).Table("lpr-vehicles").put_item(
            Item={"plate_number": "ABC1234", "vehicle_id": "T01"})

        from app import main
        from app.config import get_settings
        get_settings.cache_clear()
        main.get_storage.cache_clear()
        yield TestClient(main.app), s3


def post(client, data, key=API_KEY, name="car.jpg", ctype="image/jpeg"):
    return client.post("/recognize", headers={"X-API-Key": key},
                       files={"file": (name, data, ctype)})


def test_recognizes_plate_and_stores_everything(client):
    c, s3 = client
    r = post(c, make_plate_image("ABC1234"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plate_number"] == "ABC1234"
    assert body["status"] == "success"
    assert body["vehicle_match"] is True and body["vehicle_id"] == "T01"
    assert body["timestamp"].endswith("Z")

    # image is in S3, encrypted, and the key contains no plate text
    head = s3.head_object(Bucket="lpr-test-bucket", Key=body["image_key"])
    assert head["ServerSideEncryption"] == "aws:kms"
    assert "ABC1234" not in body["image_key"]

    # record readable, history by plate works, presigned URL works
    rec = c.get(f"/records/{body['record_id']}", headers={"X-API-Key": API_KEY}).json()
    assert rec["plate_number"] == "ABC1234"
    hist = c.get("/plates/abc-1234/records", headers={"X-API-Key": API_KEY}).json()
    assert len(hist) == 1
    url = c.get(f"/records/{body['record_id']}/image", headers={"X-API-Key": API_KEY}).json()
    assert url["url"].startswith("https://") and url["expires_in"] == 300


def test_unregistered_plate_is_not_a_match(client):
    c, _ = client
    body = post(c, make_plate_image("XYZ5678")).json()
    assert body["plate_number"] == "XYZ5678"
    assert body["vehicle_match"] is False


def test_tilted_plate_and_zero_vs_letter_o(client):
    c, _ = client
    body = post(c, make_plate_image("TRK9001", angle=6)).json()
    assert body["plate_number"] == "TRK9001"


def test_no_plate_in_image(client):
    c, _ = client
    blank = cv2.imencode(".jpg", np.full((150, 300, 3), 120, np.uint8))[1].tobytes()
    body = post(c, blank).json()
    assert body["status"] == "no_plate_found"
    assert body["plate_number"] is None
    assert body["vehicle_match"] is None


def test_rejects_missing_or_wrong_key(client):
    c, _ = client
    assert post(c, make_plate_image(), key="wrong").status_code == 401
    r = c.post("/recognize", files={"file": ("a.jpg", make_plate_image(), "image/jpeg")})
    assert r.status_code == 401


def test_rejects_non_image_even_with_image_content_type(client):
    c, _ = client
    r = post(c, b"<?php evil(); ?>" * 20, ctype="image/jpeg")
    assert r.status_code == 400
    assert "JPEG or PNG" in r.json()["detail"]


def test_rejects_oversized_upload(client, monkeypatch):
    c, _ = client
    big = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024 + 10)
    assert post(c, big).status_code == 400


def test_delete_removes_record_and_image(client):
    c, s3 = client
    body = post(c, make_plate_image()).json()
    r = c.delete(f"/records/{body['record_id']}", headers={"X-API-Key": API_KEY})
    assert r.status_code == 204
    assert c.get(f"/records/{body['record_id']}", headers={"X-API-Key": API_KEY}).status_code == 404
    with pytest.raises(s3.exceptions.ClientError):
        s3.head_object(Bucket="lpr-test-bucket", Key=body["image_key"])


def test_logs_never_contain_plate_text(client, caplog):
    c, _ = client
    caplog.set_level("INFO")
    post(c, make_plate_image("ABC1234"))
    assert "ABC1234" not in caplog.text
    assert "plate_fp=" in caplog.text


def test_ocr_misread_still_matches_registered_truck(client):
    """Tesseract reads DL4CAF5 as DL4CAFS (5/S swap); the fleet lookup should recover it."""
    c, _ = client
    boto3.resource("dynamodb", region_name=REGION).Table("lpr-vehicles").put_item(
        Item={"plate_number": "DL4CAF5", "vehicle_id": "T09"})
    body = post(c, make_plate_image("DL4CAF5")).json()
    assert body["vehicle_match"] is True
    assert body["vehicle_id"] == "T09"
    assert body["matched_plate"] == "DL4CAF5"
    assert body["match_type"] in ("exact", "ocr_tolerant")


def test_rekognition_engine_switch(client, monkeypatch):
    """With OCR_ENGINE=rekognition the same contract comes back (Rekognition faked)."""
    from app import ocr_rekognition
    from app.config import get_settings

    class FakeRek:
        def detect_text(self, Image):
            return {"TextDetections": [
                {"Type": "LINE", "DetectedText": "MICHIGAN", "Confidence": 99.0},
                {"Type": "LINE", "DetectedText": "ABC 1234", "Confidence": 97.2}]}

    monkeypatch.setenv("OCR_ENGINE", "rekognition")
    get_settings.cache_clear()
    monkeypatch.setattr(ocr_rekognition, "_client", FakeRek())
    c, _ = client
    body = post(c, make_plate_image("ABC1234")).json()
    assert body["plate_number"] == "ABC1234"
    assert body["confidence"] == 97.2
    assert body["vehicle_id"] == "T01"


@pytest.mark.parametrize("kw", [dict(dark=True), dict(width=160), dict(blur=2), dict(frame=False)])
def test_plate_crop_conditions(client, kw):
    """Ignores state name, slogan, sticker and frame; works on dark, small and blurry crops."""
    c, _ = client
    body = post(c, make_plate_image("HMW4471", **kw)).json()
    assert body["plate_number"] == "HMW4471", body


def test_full_scene_mode(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("INPUT_MODE", "full")
    get_settings.cache_clear()
    c, _ = client
    body = post(c, make_scene_image("ABC1234")).json()
    assert body["plate_number"] == "ABC1234"


def test_digit_context_fix():
    from app.ocr_plate import fix_digit_context
    assert fix_digit_context("TRK9OO1") == "TRK9001"
    assert fix_digit_context("7I23") == "7123"
    assert fix_digit_context("OBC123") == "OBC123"     # leading letters untouched
    assert fix_digit_context("ABO123") == "ABO123"     # letter O next to letters untouched
