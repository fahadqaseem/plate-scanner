"""All settings come from environment variables (or a .env file locally).
Never hard-code AWS keys: locally use `aws configure` / AWS_PROFILE,
in AWS use the IAM role attached to the server.
"""
import re
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "us-east-2"
    s3_bucket: str
    dynamodb_table: str
    kms_key_id: str | None = None          # None -> bucket default encryption is used
    # Optional table of your own trucks (key: plate_number). If set, /recognize
    # also returns whether the plate belongs to a registered truck.
    vehicles_table: str | None = None

    # Comma-separated API keys. Give Fahad's camera and Elisha's frontend different keys.
    api_keys: str

    max_image_bytes: int = 5 * 1024 * 1024
    min_ocr_confidence: float = 60.0       # Tesseract confidence, 0-100
    retention_days: int = 30               # DynamoDB TTL; keep in sync with S3 lifecycle
    presigned_url_seconds: int = 300

    # US plates are 2-8 alphanumerics. Tighten this for your state if you want.
    plate_regex: str = r"^[A-Z0-9]{2,8}$"

    ocr_engine: Literal["tesseract", "rekognition"] = "tesseract"
    # "auto"  = find the plate in the frame; if none is found, treat the image as a crop (default)
    # "full"  = whole camera frame only (the ESP32-P4-EYE sends 1920x1080 frames)
    # "plate" = image is already cropped to the plate
    input_mode: Literal["auto", "full", "plate"] = "auto"
    # Fixed camera: where the plate can appear, as fractions "x0,y0,x1,y1" of the frame.
    # Cuts out signage and other text. Empty = whole frame.
    plate_roi: str | None = None
    # Optional plate formats for position-based O/0, I/1, S/5... correction.
    # L = letter, D = digit, * = either. Standard Michigan: LLLDDDD.
    # Readings that fit no format are left unchanged.
    plate_formats: str | None = None
    # O->0 / I->1 when surrounded by digits (fixes the most common Tesseract error)
    digit_context_fix: bool = True
    tesseract_cmd: str | None = None       # set on Windows, e.g. C:\Program Files\Tesseract-OCR\tesseract.exe

    @field_validator("plate_roi")
    @classmethod
    def _check_roi(cls, v):
        from .locate import parse_roi
        parse_roi(v)              # raises a clear error at startup if malformed
        return v or None

    @field_validator("plate_formats")
    @classmethod
    def _check_formats(cls, v):
        if v and not all(re.fullmatch(r"[LD*]{2,8}", f.strip().upper()) for f in v.split(",")):
            raise ValueError("PLATE_FORMATS must be comma-separated patterns of L, D, *, e.g. LLLDDDD")
        return v or None

    @property
    def roi(self):
        from .locate import parse_roi
        return parse_roi(self.plate_roi)

    @property
    def plate_format_list(self) -> list[str] | None:
        if not self.plate_formats:
            return None
        return [f.strip().upper() for f in self.plate_formats.split(",") if f.strip()]

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
