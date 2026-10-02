"""Shrink uploaded images so the SRCF quota isn't spent on 12-megapixel
phone photos that are shown at 96 pixels."""

from io import BytesIO
from pathlib import PurePosixPath

from django import forms
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

PROFILE_MAX_PX = 512
EVENT_MAX_PX = 1600


def shrink_image(upload, max_px):
    """Return ``upload`` resized so neither side exceeds ``max_px``.

    Orientation from EXIF is applied, transparency is kept as PNG, and
    everything else becomes a quality-85 JPEG. Files already small enough
    are still re-encoded, which strips metadata (including GPS tags).
    """
    try:
        image = Image.open(upload)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise forms.ValidationError("That file doesn't look like an image.") from exc
    image = ImageOps.exif_transpose(image)
    image.thumbnail((max_px, max_px))
    keep_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    buffer = BytesIO()
    stem = PurePosixPath(upload.name).stem or "image"
    if keep_alpha:
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        name, content_type = f"{stem}.png", "image/png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
        name, content_type = f"{stem}.jpg", "image/jpeg"
    return InMemoryUploadedFile(
        buffer, upload.field_name if hasattr(upload, "field_name") else None,
        name, content_type, buffer.getbuffer().nbytes, None,
    )
