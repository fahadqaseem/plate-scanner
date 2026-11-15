# License Plate Recognition Backend (FastAPI + OpenCV + Tesseract + AWS)

Green button on the FREE-WILi → ESP32-P4-EYE takes a 1920×1080 photo → plate text out → image in S3, record in DynamoDB, JSON to the frontend.

```
FREE-WILi button ─USB─▶ bridge/freewili_bridge.py ─USB "T"─▶ ESP32-P4-EYE camera   (Fahad's plate-scanner repo)
  (laptop at the garage)        ▲  └─ spool/ (queued until uploaded)     │
                                └────────── JPEG frame over USB ◀────────┘
                                │
                                └──POST /recognize──▶ FastAPI ──▶ locate plate in the frame (OpenCV)
                                   (HTTPS, camera key)    │        └▶ isolate characters ─▶ Tesseract ─▶ fix 0/O ─▶ validate
                                                          ├──▶ S3 (image, KMS-encrypted, auto-deleted after 30 days)
                                                          ├──▶ DynamoDB (record, KMS-encrypted, TTL)
                                                          └──▶ JSON ─▶ Elisha's frontend
```

## Project layout

| File | What it does |
|---|---|
| `app/main.py` | FastAPI endpoints |
| `app/locate.py` | **Finds the plate in a full camera frame** (rows of characters inside a plate-shaped rectangle), optional fixed ROI |
| `app/ocr_plate.py` | **Plate crop → text**: isolates the big characters, ignores state name/slogan/stickers/frame; `recognize_plate_scene` = locate + read |
| `app/ocr.py` | Upload validation (magic bytes, size limits), shared types |
| `app/ocr_rekognition.py` | Optional Amazon Rekognition engine (`OCR_ENGINE=rekognition`) |
| `app/storage.py` | S3 + DynamoDB, presigned URLs, vehicle matching |
| `app/security.py` | API-key auth, plate-free logging |
| `app/config.py` | Settings from environment / `.env` |
| `infra/aws-resources.yaml` | CloudFormation: KMS key, private S3, DynamoDB, IAM policy |
| `tests/test_api.py` | End-to-end tests (real OCR, AWS faked with moto) |
| `bridge/freewili_bridge.py` | **Runs at the garage**: FREE-WILi button → camera → queue → API (uses Fahad's USB protocol) |
| `tests/test_bridge.py` | Bridge + full 1920×1080 frames end to end (serial faked, real OCR, AWS faked) |
| `examples/send_image.py` | Send one saved photo to the API by hand |
| `examples/add_vehicle.py` | Register your trucks for match/no-match |

---

## Step 1: Run it on your laptop (no AWS needed)

**Install Tesseract** (the OCR engine is a separate program):
- Mac: `brew install tesseract`
- Ubuntu: `sudo apt install tesseract-ocr`
- Windows: installer from the UB-Mannheim Tesseract page, then set `TESSERACT_CMD` in `.env`

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q                          # should print: 30 passed
```

The tests run the real OCR on synthetic plate images and fake S3/DynamoDB, so they work without an AWS account.

## Step 2: Create the AWS resources

You need the AWS CLI installed and `aws configure` done with your account.

```bash
aws cloudformation deploy \
  --template-file infra/aws-resources.yaml \
  --stack-name lpr \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides UseCustomerManagedKey=true RetentionDays=30

aws cloudformation describe-stacks --stack-name lpr --query "Stacks[0].Outputs"
```

Copy the outputs (bucket name, table names, KMS key ARN) into `.env` (start from `.env.example`).

Generate API keys, one for the camera and one for the frontend:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Step 3: Run the API against real AWS

```bash
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000/docs. FastAPI gives you a test page for every endpoint.

```bash
export API_KEY=<camera key>
python examples/add_vehicle.py --table lpr-vehicles --plate "ABC-1234" --vehicle-id T01
python examples/send_image.py --file my_car_photo.jpg
```

## Step 4: Deploy

**Cheapest simple option: one small EC2 instance with Docker**
```bash
docker build -t lpr-api .
docker run -d -p 8000:8000 --env-file .env --restart unless-stopped lpr-api
```
- Attach an IAM role to the instance that has the `lpr-backend` policy from the stack. Then you need **no AWS keys in `.env`**.
- Put HTTPS in front of it: Caddy (automatic free certificate) or an Application Load Balancer with an ACM certificate. Don't expose plain HTTP on the internet.
- Security group: allow 443 only. Allow 22 from your IP only, or use SSM Session Manager instead of SSH.

---

## Step 5: Connect the FREE-WILi camera (Fahad's hardware)

The boards come already flashed (firmware: [fahadqaseem/plate-scanner](https://github.com/fahadqaseem/plate-scanner)). On the laptop at the garage:

1. Plug the ESP32-P4-EYE's **Debug** USB-C port and the FREE-WILi into the **same** laptop (data cables, not charge-only). Close `idf.py monitor` or any serial terminal.
2. Install and run the bridge:
   ```bash
   pip install -r bridge/requirements.txt
   export API_URL=https://<your-api-domain>   # http://localhost:8000 while testing
   export API_KEY=<camera key>                # the camera's own key, not the frontend's
   python bridge/freewili_bridge.py --source garage-cam-1
   ```
3. Press the green button. You'll see:
   ```
   FREE-WILi button -> capture
   captured 245 KB  id=4f9c2a8e
   [2026-11-14T18:00:00.123Z] ABC1234  success  conf=90.0  -> truck T01 (exact)
   ```

Camera only (no FREE-WILi): `python bridge/freewili_bridge.py --manual` and press Enter to capture.

What the bridge does for you:
- **Never loses a capture.** Each JPEG is written to `spool/` first, then uploaded. If the API or internet is down, captures wait there and upload when it's back, oldest first. They're deleted once uploaded (`--keep-copies DIR` only if you really want local copies).
- **No duplicates on retry.** Each capture has a `capture_id`. If the response is lost and the bridge retries, the API returns the existing record.
- **Real capture time.** Queued uploads keep the time the photo was taken.
- **Images the API rejects (400)** go to `spool/rejected/` with the reason instead of retrying forever.
- Reconnects if a USB cable is pulled.

**Fixed camera? Set an ROI.** The camera sees the same spot every time, so tell the backend where the plate can be:
```
PLATE_ROI=0.25,0.40,0.75,0.95     # x0,y0,x1,y1 as fractions of the frame
```
Take one test photo, check where plates land, and leave some margin. This removes signage, DOT numbers and company names from the search, and it's the most effective accuracy setting for a fixed camera.

**Your fleet is all one state?** `PLATE_FORMATS=LLLDDDD` (standard Michigan: 3 letters, 4 digits) fixes look-alike characters by position (`TRK9OOL` → `TRK9001`). Plates that don't fit the format are left as read.

## API contract (for Elisha)

Every request except `/health` needs the header `X-API-Key: <key>`.

### `POST /recognize`
multipart/form-data, field `file` = JPEG or PNG (max 5 MB). Either a full camera frame or a plate crop (`INPUT_MODE=auto` handles both).

Optional query parameters (the bridge sends all three):
| Param | Meaning |
|---|---|
| `source` | camera name, e.g. `garage-cam-1` (max 32 chars) |
| `captured_at` | ISO 8601 time with timezone, e.g. `2026-11-14T18:00:00.123Z`. Max 5 min in the future, not older than the retention period. Default: time received |
| `capture_id` | UUID. Becomes the `record_id`. Sending the same one again returns the existing record (safe retries) |

```json
{
  "record_id": "4f9c2a8e-1b7d-4c55-9e2a-6d1f0b3c7a11",
  "plate_number": "ABC1234",
  "confidence": 91.5,
  "image_key": "uploads/2026/11/14/4f9c2a8e-1b7d-4c55-9e2a-6d1f0b3c7a11.jpg",
  "status": "success",
  "timestamp": "2026-11-14T18:00:00.123456Z",
  "vehicle_match": true,
  "vehicle_id": "T01",
  "matched_plate": "ABC1234",
  "match_type": "exact"
}
```

| `status` | Meaning | `plate_number` |
|---|---|---|
| `success` | Valid plate, confidence ≥ threshold | text |
| `low_confidence` | Something plate-like was read, but it's uncertain. Show it as "needs review". | text |
| `no_plate_found` | Nothing readable | `null` |

`vehicle_match` is `null` when matching isn't configured or the status isn't success. `match_type: "ocr_tolerant"` means the OCR text differed slightly (for example 5 vs S) but matched exactly one registered truck.

Errors: `400` bad image, `401` bad/missing key, `502` AWS storage failed (retry).

> Changed from the draft contract: `record_id` is a UUID, not `"001"`. Sequential IDs collide when two requests arrive at once and let anyone guess other records' IDs.

### Other endpoints
| Method & path | Returns |
|---|---|
| `GET /records/{record_id}` | the stored record (includes `source`, `vehicle_id`, `matched_plate`) |
| `GET /records/{record_id}/image` | `{"url": "...", "expires_in": 300}`: a 5-minute link to view the image |
| `GET /plates/{plate}/records` | recent records for one plate, newest first |
| `DELETE /records/{record_id}` | deletes record and image (204) |
| `GET /health` | `{"status":"ok"}` |

The frontend never gets AWS credentials or a public bucket. It asks the backend for a short-lived presigned link.

---

## Privacy and security checklist (what's implemented where)

| Requirement | How |
|---|---|
| Encryption at rest (KMS) | S3 default SSE-KMS with Bucket Keys; DynamoDB SSE with the same key; bucket policy **rejects** unencrypted uploads |
| Encryption in transit | boto3 uses HTTPS; bucket policy **denies** any non-TLS request; put HTTPS in front of the API |
| IAM least privilege | `lpr-backend` policy: only the needed S3/DynamoDB/KMS actions, only on these resources |
| Private bucket | All four Block Public Access settings on, ACLs disabled |
| Restricted DB access | Only the backend role can read/write; frontend goes through the API |
| No PII in logs | Logs show `record_id` and a short hash of the plate (`plate_fp`), never the plate text; S3 keys contain no plate |
| Retention / deletion | S3 lifecycle deletes images after N days; DynamoDB TTL (`expires_at`) deletes records; `DELETE /records/{id}` for on-demand removal |
| Authenticated API | `X-API-Key`, constant-time comparison, separate keys per client so one can be revoked |
| Input validation | Magic-byte check (not the client's content type), size and dimension limits |

**Before going live:**
- Restrict CORS in `main.py` to Elisha's real domain.
- Store API keys in **SSM Parameter Store** (SecureString), not in a file on the server.
- Only store plates of your own fleet long-term if possible. Many US states regulate stored plate-reader data.

---

## Accuracy: honest expectations

### Full camera frames (what the ESP32-P4-EYE sends: 1920×1080)

Default `INPUT_MODE=auto`. The backend first finds the plate:
1. It finds rows of character-shaped blobs (similar height, same line, close together), for dark-on-light and light-on-dark plates.
2. It looks for a plate-shaped rectangle around each row. Text painted on the truck (company name, DOT number) usually has no rectangle, so it ranks lower and its confidence is cut by 30%.
3. It crops each candidate at full resolution and reads it with the plate-crop OCR below.
4. If nothing is found, it treats the whole image as a crop, so pre-cropped images still work.

Measured on synthetic 1920×1080 garage frames: truck body, grille, tail lights, a "LOGISTICS CO" sign, a DOT number, an "EXIT 3" sign, and a Michigan-style plate. 8 plates per condition, no ROI:

| Plate width in the 1920 px frame | Read correctly | Returned as `success` |
|---|---|---|
| 400 px | 8/8 | 8/8 |
| 300 px | 8/8 | 8/8 |
| 220 px | 8/8 | 8/8 |
| 160 px | 7/8 | 7/8 |
| 120 px | 8/8 | 8/8 |
| 280 px, off-centre | 8/8 | 8/8 |
| 300 px, dark garage (55% brightness) | 8/8 | 8/8 |
| 300 px, white-on-dark plate | 8/8 | 7/8 (one read right at low confidence) |
| 300 px, 5° tilt | 7/8 | 7/8 |
| 300 px, blurred | 8/8 | 8/8 |

About 1 s per frame on a laptop CPU. Before this locator, the old full-scene mode read **0/56** of these frames, and the plate-crop mode can't handle full frames at all.

With **no plate in view**, the "EXIT 3" sign comes back as `low_confidence`, never `success`, so it never triggers a truck match.

### Plate crops

For a plate crop (or each candidate the locator found) the OCR works like this:
1. It upscales the crop and evens out the lighting.
2. It finds the blobs that are sized and shaped like the big registration characters, which drops the state name, slogan, stickers, bolts and frame.
3. It splits characters that touch and straightens a tilted row.
4. It redraws only those characters on a clean background for Tesseract.
5. It fixes O/I that sit between digits, so `9OO1` becomes `9001`.

Measured on synthetic Michigan-style plate crops (8 plates per condition):

| Condition | Correct |
|---|---|
| Clean, 600 px / 200 px wide, dark plate, blur, 5° tilt, other font, no frame | 8/8 each |
| 120 px wide | 7/8 |
| 10° tilt, heavy blur, heavy noise, dark+small+blurry | 7–8/8 |
| **90 px wide** | **5/8** |

For crops, aim for at least ~150 px of plate width. For full frames, mount the camera so a plate is **at least ~200 px wide** in the 1920 px frame. That leaves headroom for real-world blur and glare.

These are synthetic images. Real plates add glare, dirt, odd fonts, stacked characters and state logos between characters, so test with ~20 real crops from your camera before trusting these numbers. If real accuracy is poor:
1. Set `PLATE_ROI`, move the camera closer, and light the plate at night.
2. Registered-vehicle matching (already built): `ocr_tolerant` recovers 5/S, 8/B, 0/O swaps for your own trucks.
3. `OCR_ENGINE=rekognition` (already wired in; add `rekognition:DetectText` to the IAM policy).
4. PaddleOCR or a plate-specific model.

`INPUT_MODE=plate` skips the locator if a camera ever sends tight crops only. `INPUT_MODE=full` disables the whole-image fallback.
