"""S3 (images) + DynamoDB (records). Encryption, TTL-based retention, presigned reads."""
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from .config import Settings


class Storage:
    def __init__(self, settings: Settings):
        self.s = settings
        cfg = Config(region_name=settings.aws_region, retries={"max_attempts": 3, "mode": "standard"},
                     signature_version="s3v4")
        # boto3 always talks to AWS over HTTPS (TLS) by default
        self.s3 = boto3.client("s3", config=cfg)
        self.table = boto3.resource("dynamodb", config=cfg).Table(settings.dynamodb_table)

    # ---------- S3 ----------

    def put_image(self, record_id: str, data: bytes, fmt: str, captured_at: datetime) -> str:
        ext = "jpg" if fmt == "jpeg" else "png"
        # Key holds no plate text: plate is PII, keys show up in logs and listings
        key = f"uploads/{captured_at:%Y/%m/%d}/{record_id}.{ext}"
        extra = {"ServerSideEncryption": "aws:kms"}
        if self.s.kms_key_id:
            extra["SSEKMSKeyId"] = self.s.kms_key_id
            extra["BucketKeyEnabled"] = True  # cuts KMS request costs
        self.s3.put_object(Bucket=self.s.s3_bucket, Key=key, Body=data,
                           ContentType=f"image/{fmt}", **extra)
        return key

    def presigned_image_url(self, image_key: str) -> str:
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.s.s3_bucket, "Key": image_key},
            ExpiresIn=self.s.presigned_url_seconds)

    def delete_image(self, image_key: str) -> None:
        self.s3.delete_object(Bucket=self.s.s3_bucket, Key=image_key)

    # ---------- DynamoDB ----------

    def put_record(self, item: dict) -> None:
        item = dict(item)
        item["expires_at"] = int(time.time()) + self.s.retention_days * 86400  # TTL attribute
        for k, v in list(item.items()):
            if isinstance(v, float):
                item[k] = Decimal(str(v))
            if v is None:
                del item[k]  # DynamoDB GSI keys can't be null; just omit
        # never overwrite an existing record
        self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(record_id)")

    def get_record(self, record_id: str) -> dict | None:
        return self.table.get_item(Key={"record_id": record_id}).get("Item")

    def find_by_plate(self, plate: str, limit: int = 20) -> list[dict]:
        resp = self.table.query(IndexName="plate_number-index",
                                KeyConditionExpression=Key("plate_number").eq(plate),
                                ScanIndexForward=False, Limit=limit)
        return resp["Items"]

    def lookup_vehicle(self, plate: str) -> tuple[dict | None, str | None]:
        """Find the registered truck for an OCR reading.

        Returns (vehicle, how) where how is 'exact' or 'ocr_tolerant'.
        Tolerant matching treats 0/O, 1/I, 5/S, 8/B, 2/Z, 6/G as the same and only
        accepts a match if exactly one registered plate fits (never guesses).
        Fine for fleets up to a few thousand trucks (list cached for 60 s).
        """
        if not self.s.vehicles_table or not plate:
            return None, None
        if not hasattr(self, "_vehicles"):
            self._vehicles = boto3.resource(
                "dynamodb", region_name=self.s.aws_region).Table(self.s.vehicles_table)
            self._fleet_cache = (0.0, [])

        item = self._vehicles.get_item(Key={"plate_number": plate}).get("Item")
        if item:
            return item, "exact"

        loaded_at, fleet = self._fleet_cache
        if time.time() - loaded_at > 60:
            resp = self._vehicles.scan()
            fleet = resp["Items"]
            while "LastEvaluatedKey" in resp:
                resp = self._vehicles.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
                fleet += resp["Items"]
            self._fleet_cache = (time.time(), fleet)

        target = canonical_plate(plate)
        hits = [v for v in fleet if canonical_plate(v["plate_number"]) == target]
        return (hits[0], "ocr_tolerant") if len(hits) == 1 else (None, None)

    def delete_record(self, record_id: str) -> bool:
        item = self.get_record(record_id)
        if not item:
            return False
        if item.get("image_key"):
            self.delete_image(item["image_key"])
        self.table.delete_item(Key={"record_id": record_id})
        return True


_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                             "S": "5", "B": "8", "Z": "2", "G": "6"})


def canonical_plate(plate: str) -> str:
    return plate.upper().translate(_CONFUSABLE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
