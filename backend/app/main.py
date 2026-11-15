"""FastAPI app: camera/frontend upload an image, get the plate back as JSON.

Endpoints (all need header X-API-Key except /health):
  POST   /recognize                  multipart field "file" (JPEG/PNG)
                                     optional query: source, captured_at, capture_id
  GET    /records/{record_id}        one stored record
  GET    /records/{record_id}/image  short-lived presigned URL to view the image
  GET    /plates/{plate}/records     history for a plate
  DELETE /records/{record_id}        delete record + image (right-to-delete)
  GET    /health
"""
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import Settings, get_settings
from .ocr import InvalidImageError, clean_text, decode_image, detect_format
from .ocr_plate import recognize_plate_crop, recognize_plate_scene
from .ocr_rekognition import recognize_plate_rekognition
from .security import get_logger, plate_fingerprint, require_api_key
from .storage import Storage, utcnow

log = get_logger("lpr")

app = FastAPI(title="License Plate Recognition API", version="1.0.0")

# Lock this down to Elisha's real frontend origin before going live
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"],
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["X-API-Key", "Content-Type"])


@lru_cache
def get_storage() -> Storage:
    return Storage(get_settings())


Status = Literal["success", "low_confidence", "no_plate_found"]


class RecognizeResponse(BaseModel):
    record_id: str
    plate_number: str | None
    confidence: float
    image_key: str
    status: Status
    timestamp: str
    vehicle_match: bool | None = None      # None = matching not configured
    vehicle_id: str | None = None
    matched_plate: str | None = None       # registered plate (may differ from OCR text)
    match_type: Literal["exact", "ocr_tolerant"] | None = None


class RecordResponse(BaseModel):
    record_id: str
    plate_number: str | None = None
    confidence: float
    image_key: str
    status: str
    timestamp: str
    source: str | None = None
    vehicle_id: str | None = None
    matched_plate: str | None = None


MAX_CLOCK_SKEW = timedelta(minutes=5)


def _parse_captured_at(value: str | None, settings: Settings) -> datetime:
    """Capture time sent by the camera bridge (so queued uploads keep their real time)."""
    now = utcnow()
    if not value:
        return now
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "captured_at must be an ISO 8601 time, e.g. 2026-11-14T18:00:00Z")
    if t.tzinfo is None:
        raise HTTPException(400, "captured_at needs a timezone (end it with Z for UTC)")
    t = t.astimezone(timezone.utc)
    if t > now + MAX_CLOCK_SKEW or t < now - timedelta(days=settings.retention_days):
        raise HTTPException(400, "captured_at is in the future or older than the retention period")
    return t


def _parse_capture_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(400, "capture_id must be a UUID")


def _response_from_item(item: dict, settings: Settings) -> RecognizeResponse:
    matching_on = bool(settings.vehicles_table)
    success = item["status"] == "success"
    return RecognizeResponse(
        record_id=item["record_id"], plate_number=item.get("plate_number"),
        confidence=float(item.get("confidence", 0)), image_key=item["image_key"],
        status=item["status"], timestamp=item["timestamp"],
        vehicle_match=(item.get("vehicle_id") is not None) if matching_on and success else None,
        vehicle_id=item.get("vehicle_id"), matched_plate=item.get("matched_plate"),
        match_type=item.get("match_type"))


def _run_ocr(img, settings: Settings):
    if settings.ocr_engine == "rekognition":
        return recognize_plate_rekognition(img, settings.plate_regex, settings.aws_region)
    fmts = settings.plate_format_list
    if settings.input_mode == "plate":
        return recognize_plate_crop(img, settings.plate_regex, settings.tesseract_cmd,
                                    settings.digit_context_fix, fmts)
    return recognize_plate_scene(img, settings.plate_regex, settings.tesseract_cmd,
                                 settings.digit_context_fix, roi=settings.roi,
                                 crop_fallback=settings.input_mode == "auto", plate_formats=fmts)


def _to_record(item: dict) -> RecordResponse:
    return RecordResponse(
        record_id=item["record_id"], plate_number=item.get("plate_number"),
        confidence=float(item.get("confidence", 0)), image_key=item["image_key"],
        status=item["status"], timestamp=item["timestamp"], source=item.get("source"),
        vehicle_id=item.get("vehicle_id"), matched_plate=item.get("matched_plate"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recognize", response_model=RecognizeResponse)
async def recognize(
    file: UploadFile = File(...),
    source: str = "camera",
    captured_at: str | None = None,     # ISO 8601 capture time from the camera bridge
    capture_id: str | None = None,      # UUID from the bridge; a retried upload returns the same record
    api_key: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    storage: Storage = Depends(get_storage),
):
    # read at most limit+1 bytes so a huge upload can't eat memory
    data = await file.read(settings.max_image_bytes + 1)
    try:
        img = decode_image(data, settings.max_image_bytes)
        fmt = detect_format(data)
    except InvalidImageError as e:
        raise HTTPException(400, str(e))

    capture_id = _parse_capture_id(capture_id)
    captured = _parse_captured_at(captured_at, settings)
    if capture_id:
        existing = await run_in_threadpool(storage.get_record, capture_id)
        if existing:
            # the bridge retried after a lost response: don't OCR or store twice
            log.info("replay record_id=%s", capture_id)
            return _response_from_item(existing, settings)
    record_id = capture_id or str(uuid.uuid4())

    # OCR is CPU/network-bound; run it off the event loop
    try:
        result = await run_in_threadpool(_run_ocr, img, settings)
    except Exception:
        log.exception("ocr failed record_id=%s engine=%s", record_id, settings.ocr_engine)
        raise HTTPException(500, "OCR engine error")

    if result.plate is None:
        status: Status = "no_plate_found"
    elif result.confidence < settings.min_ocr_confidence:
        status = "low_confidence"
    else:
        status = "success"

    try:
        image_key = await run_in_threadpool(storage.put_image, record_id, data, fmt, captured)
        vehicle, match_type = None, None
        if status == "success":
            vehicle, match_type = await run_in_threadpool(storage.lookup_vehicle, result.plate)
        item = {
            "record_id": record_id,
            "plate_number": result.plate,
            "confidence": result.confidence,
            "image_key": image_key,
            "status": status,
            "timestamp": captured.isoformat().replace("+00:00", "Z"),
            "source": source[:32],
            "vehicle_id": vehicle.get("vehicle_id") if vehicle else None,
            # the registered plate; can differ from the OCR text for ocr_tolerant matches
            "matched_plate": vehicle["plate_number"] if vehicle else None,
            "match_type": match_type,
        }
        await run_in_threadpool(storage.put_record, item)
    except Exception:
        # log the record id only: no plate text, no image bytes, no AWS error body
        log.exception("storage failed record_id=%s", record_id)
        raise HTTPException(502, "Could not store the result, please retry")

    log.info("recognized record_id=%s status=%s plate_fp=%s conf=%.1f tried=%d",
             record_id, status, plate_fingerprint(result.plate), result.confidence, result.candidates_tried)

    return _response_from_item(item, settings)


@app.get("/records/{record_id}", response_model=RecordResponse)
def get_record(record_id: str, api_key: str = Depends(require_api_key),
               storage: Storage = Depends(get_storage)):
    item = storage.get_record(record_id)
    if not item:
        raise HTTPException(404, "Record not found")
    return _to_record(item)


@app.get("/records/{record_id}/image")
def get_image_url(record_id: str, api_key: str = Depends(require_api_key),
                  storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings)):
    item = storage.get_record(record_id)
    if not item:
        raise HTTPException(404, "Record not found")
    return {"url": storage.presigned_image_url(item["image_key"]),
            "expires_in": settings.presigned_url_seconds}


@app.get("/plates/{plate}/records", response_model=list[RecordResponse])
def plate_history(plate: str, api_key: str = Depends(require_api_key),
                  storage: Storage = Depends(get_storage)):
    return [_to_record(i) for i in storage.find_by_plate(clean_text(plate))]


@app.delete("/records/{record_id}", status_code=204)
def delete_record(record_id: str, api_key: str = Depends(require_api_key),
                  storage: Storage = Depends(get_storage)):
    if not storage.delete_record(record_id):
        raise HTTPException(404, "Record not found")
    log.info("deleted record_id=%s", record_id)
