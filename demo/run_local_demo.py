"""Demo launcher: Anvita's FastAPI backend + fake AWS (moto) + the frontend, on one port.
usage: python demo/run_local_demo.py /path/to/lpr-backend/backend
Then open http://localhost:8000/demo/ . Data lives in memory and is lost on exit.
Adds (without touching her code): GET /demo-api/latest, GET /demo-api/image/{id}, static /demo/.
"""
import os, sys
from pathlib import Path
BACKEND = Path(sys.argv[1]).resolve()
FRONT = Path(__file__).resolve().parent.parent / "frontend"
for k, v in {"AWS_REGION":"us-east-2","AWS_DEFAULT_REGION":"us-east-2","AWS_ACCESS_KEY_ID":"testing",
  "AWS_SECRET_ACCESS_KEY":"testing","S3_BUCKET":"lpr-demo","DYNAMODB_TABLE":"lpr-records",
  "VEHICLES_TABLE":"lpr-vehicles","API_KEYS":"demo-camera-key",
  "MIN_OCR_CONFIDENCE":"75"}.items():
    os.environ[k] = v
import boto3, uvicorn
from moto import mock_aws
R = "us-east-2"
with mock_aws():
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
    from fastapi import Response, HTTPException
    from fastapi.staticfiles import StaticFiles
    ddb = boto3.resource("dynamodb", region_name=R).Table("lpr-records")
    s3 = boto3.client("s3", region_name=R)

    @app.get("/demo-api/latest")
    def latest(n: int = 12):
        items = ddb.scan().get("Items", [])
        items.sort(key=lambda i: i.get("timestamp", ""), reverse=True)
        keep = ("record_id","plate_number","confidence","status","timestamp","source","vehicle_id","vehicle_match")
        return [{k: (float(i[k]) if k == "confidence" and k in i else i.get(k)) for k in keep} for i in items[:n]]

    @app.get("/demo-api/image/{rid}")
    def image(rid: str):
        it = ddb.get_item(Key={"record_id": rid}).get("Item")
        if not it: raise HTTPException(404)
        return Response(s3.get_object(Bucket="lpr-demo", Key=it["image_key"])["Body"].read(), media_type="image/jpeg")

    app.mount("/demo", StaticFiles(directory=str(FRONT), html=True), name="demo")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
