"""Small private previews. Call only AFTER checking current object permissions."""

import warnings
from hashlib import sha256
from io import BytesIO
from threading import BoundedSemaphore

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, HttpResponse
from PIL import Image, ImageOps, UnidentifiedImageError

# At most one decoder per web worker. Requests that miss this slot still get
# their authorized original; they must not queue behind expensive image work.
_preview_slot = BoundedSemaphore(1)
MAX_DECODE_PIXELS = 4_000_000


def render_preview(file, size):
    if not _preview_slot.acquire(blocking=False):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with file.open("rb") as source, Image.open(source) as image:
                # JPEG draft decoding reduces pixels BEFORE allocating them.
                # Other large formats retain the original rather than decoding
                # hundreds of megabytes merely to make a small thumbnail.
                image.draft("RGB", (size, size))
                if image.width * image.height > MAX_DECODE_PIXELS:
                    return None
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                with ImageOps.exif_transpose(image) as oriented:
                    with oriented.convert("RGBA") as rgba:
                        with Image.new("RGB", oriented.size, "white") as result:
                            with rgba.getchannel("A") as alpha:
                                result.paste(rgba, mask=alpha)
                            with BytesIO() as output:
                                result.save(output, format="JPEG", quality=78)
                                return output.getvalue()
    finally:
        _preview_slot.release()


def private_image_response(request, file):
    size = (
        480
        if request.GET.get("thumb") == "1"
        else 960
        if request.GET.get("preview") == "1"
        else None
    )
    response = None
    if size:
        # Private storage never overwrites names; replacements get new names.
        namespace = str(settings.PRIVATE_MEDIA_ROOT) + str(
            getattr(settings, "PRIVATE_STORAGE_BACKEND", "local")
        )
        key = (
            "image-preview-v2:"
            + sha256(f"{namespace}:{file.name}:{size}".encode()).hexdigest()
        )
        try:
            data = cache.get(key)
        except Exception:
            data = None
        if data is None:
            try:
                data = render_preview(file, size)
                if data is not None and len(data) <= 512_000:
                    try:
                        cache.set(key, data, timeout=900)
                    except Exception:
                        pass  # Cache availability must not determine image access.
            except (
                UnidentifiedImageError,
                OSError,
                ValueError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ):
                data = None
        if data is not None:
            response = HttpResponse(data, content_type="image/jpeg")
    if response is None:
        response = FileResponse(file.open("rb"), as_attachment=False)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
