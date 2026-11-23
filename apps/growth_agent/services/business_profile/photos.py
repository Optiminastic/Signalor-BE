"""
Photos for a brand's Google Business Profile: checked, then kept in a private
S3-compatible bucket (``GBP_S3_*``), shown back through short-lived signed links.

Google's own limits are applied before anything is stored: JPG or PNG, 10 KB to
5 MB, at least 250 px on each side. One logo and one cover per brand (a new one
replaces the old), and up to 10 other photos.
"""

from __future__ import annotations

import hashlib
import io
import uuid

from django.conf import settings
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from ...models import BusinessProfilePhoto, GrowthProgram

MIN_BYTES = 10 * 1024
MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 250
MAX_OTHER = 10
LINK_SECONDS = 15 * 60
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png")}
SINGLE_KINDS = (BusinessProfilePhoto.Kind.LOGO, BusinessProfilePhoto.Kind.COVER)


class PhotoError(Exception):
    """The photo can't be accepted. The message is safe to show the owner."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def is_configured() -> bool:
    return bool(getattr(settings, "GBP_S3_BUCKET", "") and getattr(settings, "GBP_S3_ACCESS_KEY_ID", ""))


def _client():
    import boto3  # noqa: PLC0415  (only needed when photos are used)

    return boto3.client(
        "s3",
        region_name=getattr(settings, "GBP_S3_REGION", "") or None,
        endpoint_url=getattr(settings, "GBP_S3_ENDPOINT_URL", "") or None,
        aws_access_key_id=settings.GBP_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.GBP_S3_SECRET_ACCESS_KEY,
    )


def inspect(data: bytes) -> tuple[str, str, int, int]:
    """(content type, extension, width, height) if the bytes are an acceptable photo; else PhotoError."""
    if len(data) < MIN_BYTES:
        raise PhotoError("That photo is too small (under 10 KB). Use a clearer image.")
    if len(data) > MAX_BYTES:
        raise PhotoError("That photo is over 5 MB. Use a smaller file.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            fmt, (width, height) = image.format, image.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise PhotoError("That file isn't a readable image.") from exc
    if fmt not in FORMATS:
        raise PhotoError("Use a JPG or PNG photo.")
    if min(width, height) < MIN_SIDE:
        raise PhotoError(f"Photos need to be at least {MIN_SIDE} px on each side.")
    content_type, extension = FORMATS[fmt]
    return content_type, extension, width, height


def _check_room(program: GrowthProgram, kind: str) -> None:
    if kind == BusinessProfilePhoto.Kind.OTHER:
        others = program.business_profile_photos.filter(kind=BusinessProfilePhoto.Kind.OTHER).count()
        if others >= MAX_OTHER:
            raise PhotoError(f"You can add up to {MAX_OTHER} photos besides the logo and cover.")


def save(program: GrowthProgram, data: bytes, kind: str) -> BusinessProfilePhoto:
    """Check and store one photo. A new logo or cover replaces the old one."""
    if not is_configured():
        raise PhotoError("Photo storage isn't set up on the server yet.", status=503)
    content_type, extension, width, height = inspect(data)
    _check_room(program, kind)
    key = f"gbp/{program.organization_id}/{uuid.uuid4().hex}.{extension}"
    _client().put_object(Bucket=settings.GBP_S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    with transaction.atomic():
        replaced = list(program.business_profile_photos.filter(kind=kind)) if kind in SINGLE_KINDS else []
        photo = BusinessProfilePhoto.objects.create(
            program=program,
            kind=kind,
            storage_key=key,
            content_type=content_type,
            size=len(data),
            width=width,
            height=height,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        for old in replaced:
            delete(old)
    return photo


def delete(photo: BusinessProfilePhoto) -> None:
    """Remove the photo from the bucket and the database."""
    if is_configured():
        _client().delete_object(Bucket=settings.GBP_S3_BUCKET, Key=photo.storage_key)
    photo.delete()


def link(photo: BusinessProfilePhoto) -> str:
    """A signed link valid for 15 minutes, or "" when storage isn't set up."""
    if not is_configured():
        return ""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.GBP_S3_BUCKET, "Key": photo.storage_key},
        ExpiresIn=LINK_SECONDS,
    )
