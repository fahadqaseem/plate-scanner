"""Demo launcher: Anvita's FastAPI backend + the frontend, on one port.
usage: python demo/run_local_demo.py /path/to/lpr-backend/backend [--aws]

default  fake AWS (moto, in memory, free). Records vanish when the API stops.
--aws    REAL AWS using the credentials from `aws configure` and the settings in demo/aws.env
         (gitignored: S3_BUCKET, DYNAMODB_TABLE, VEHICLES_TABLE, KMS_KEY_ID, API_KEYS, AWS_REGION).

Then open http://localhost:8000/demo/ . Adds, without touching her code:
GET /demo-api/latest, GET /demo-api/image/{id}, static /demo/.
"""
import contextlib, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = Path(sys.argv[1]).resolve()
REAL = "--aws" in sys.argv
FRONT = HERE.parent / "frontend"
R = "us-east-2"

if REAL:
    for line in (HERE / "aws.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    R = os.environ.get("AWS_REGION", R)
    os.environ["AWS_DEFAULT_REGION"] = R
else:
    os.environ.update({"AWS_REGION": R, "AWS_DEFAULT_REGION": R, "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing", "S3_BUCKET": "lpr-demo", "DYNAMODB_TABLE": "lpr-records",
        "VEHICLES_TABLE": "lpr-vehicles", "API_KEYS": "demo-camera-key"})
os.environ.setdefault("MIN_OCR_CONFIDENCE", "75")

import boto3, uvicorn

if REAL:
    ctx = contextlib.nullcontext()
else:
    from moto import mock_aws
    ctx = mock_aws()

with ctx:
    if not REAL:
        boto3.client("s3", region_name=R).create_bucket(Bucket="lpr-demo", CreateBucketConfiguration={"LocationConstraint": R})
        d = boto3.client("dynamodb", region_name=R)
        d.create_table(TableName="lpr-records", BillingMode="PAY_PER_REQUEST",
          AttributeDefinitions=[{"AttributeName":"record_id","AttributeType":"S"},{"AttributeName":"plate_number","AttributeType":"S"},{"AttributeName":"timestamp","AttributeType":"S"}],
          KeySchema=[{"AttributeName":"record_id","KeyType":"HASH"}],
          GlobalSecondaryIndexes=[{"IndexName":"plate_number-index","KeySchema":[{"AttributeName":"plate_number","KeyType":"HASH"},{"AttributeName":"timestamp","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}])
        d.create_table(TableName="lpr-vehicles", BillingMode="PAY_PER_REQUEST",
          AttributeDefinitions=[{"AttributeName":"plate_number","AttributeType":"S"}], KeySchema=[{"AttributeName":"plate_number","KeyType":"HASH"}])
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    from app.main import app
    from fastapi import Request, Response, HTTPException
    from fastapi.staticfiles import StaticFiles

    # Prefer Apple Vision for reading (macOS); keep Anvita's Tesseract pipeline as the fallback.
    try:
        sys.path.insert(0, str(HERE))
        import apple_vision_ocr
        import app.main as _m
        from app.ocr_plate import OcrResult
        _orig_run_ocr = _m._run_ocr

        def _run_ocr(img, settings):
            plate, conf, lines = apple_vision_ocr.read_plate(img, settings.plate_regex)
            if plate:
                return OcrResult(plate, round(conf, 1), plate, 1, [(l[0], round(l[1] * 100, 1)) for l in lines])
            return _orig_run_ocr(img, settings)
        _m._run_ocr = _run_ocr
        print("OCR: Apple Vision (Tesseract fallback)")
    except Exception as e:
        print("OCR: Tesseract only (", e, ")")

    ddb = boto3.resource("dynamodb", region_name=R).Table(os.environ["DYNAMODB_TABLE"])
    s3 = boto3.client("s3", region_name=R)
    bucket = os.environ["S3_BUCKET"]
    recent = []   # newest first; filled from /recognize responses (the AWS policy has no table Scan)

    @app.middleware("http")
    async def remember(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path == "/recognize" and resp.status_code == 200:
            body = b"".join([c async for c in resp.body_iterator])
            try:
                j = json.loads(body)
                recent.insert(0, {k: j.get(k) for k in ("record_id","plate_number","confidence","status","timestamp","vehicle_id","vehicle_match")})
                del recent[50:]
            except Exception:
                pass
            return Response(body, status_code=resp.status_code, headers={k: v for k, v in resp.headers.items() if k.lower() != "content-length"}, media_type=resp.media_type)
        return resp

    @app.get("/demo-api/latest")
    def latest(n: int = 12):
        return recent[:n]

    @app.get("/demo-api/image/{rid}")
    def image(rid: str):
        it = ddb.get_item(Key={"record_id": rid}).get("Item")
        if not it: raise HTTPException(404)
        return Response(s3.get_object(Bucket=bucket, Key=it["image_key"])["Body"].read(), media_type="image/jpeg")

    app.mount("/demo", StaticFiles(directory=str(FRONT), html=True), name="demo")
    print("AWS:", "REAL " + bucket if REAL else "fake (moto)")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
