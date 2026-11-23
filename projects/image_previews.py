"""Small private previews. Call only AFTER checking current object permissions."""

import warnings
from hashlib import sha256
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, HttpResponse
from PIL import Image, ImageOps, UnidentifiedImageError


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
            "image-preview-v1:"
            + sha256(f"{namespace}:{file.name}:{size}".encode()).hexdigest()
        )
        try:
            data = cache.get(key)
        except Exception:
            data = None
        if data is None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with file.open("rb") as source, Image.open(source) as image:
                        if image.width * image.height > 20_000_000:
                            raise ValueError(
                                "Image is too large to decode in a web worker."
                            )
                        image = ImageOps.exif_transpose(image)
                        image.thumbnail((size, size), Image.Resampling.LANCZOS)
                        output = BytesIO()
                        if "A" in image.getbands() or image.mode == "P":
                            rgba = image.convert("RGBA")
                            background = Image.new("RGB", image.size, "white")
                            background.paste(rgba, mask=rgba.getchannel("A"))
                            image = background
                        image.convert("RGB").save(
                            output, format="JPEG", quality=78, optimize=True
                        )
                        data = output.getvalue()
                if len(data) <= 512_000:
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
