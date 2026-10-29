"""Underlay pictures prepared for the exporters.

Both exporters draw an underlay the same way: decode the stored PNG,
fade it toward the page color by its opacity, and place the result.
Fading is done here, once, with Pillow rather than with each backend's
own alpha support, because reportlab's fill alpha does not apply to
drawImage — a PDF would otherwise show every underlay at full strength
while the PNG and the canvas show it faded.
"""

from __future__ import annotations

import base64
import io
import warnings

from PIL import Image

from netplanner.domain.entities import UNDERLAY_MAX_SIDE_PX

from .renderer import UnderlayShape
from .styles import DIAGRAM_BG

# The importer never stores anything larger than this, so a picture past
# it was put there by hand or by a damaged file. Checked against the
# header before the pixels are decoded, which is what keeps a small,
# highly compressed PNG from expanding into gigabytes here.
MAX_UNDERLAY_PIXELS = UNDERLAY_MAX_SIDE_PX * UNDERLAY_MAX_SIDE_PX


def faded_underlay(shape: UnderlayShape, size: tuple[int, int] | None = None) -> Image.Image:
    """The underlay as an opaque RGB image, faded toward the page color.

    Transparent regions of the source become page color, as they look on
    the canvas. `size` resizes the result; None keeps the stored pixels
    (the PDF exporter lets reportlab do the scaling).

    Raises:
        ValueError: the stored data is not valid base64, or the picture
            is larger than an import could have produced.
        PIL.UnidentifiedImageError: the bytes are not an image.
    """
    raw = base64.b64decode(shape.image_png_b64, validate=True)
    # Pillow warns at its own, much larger, bomb threshold. The check
    # below is the one that decides; the warning would only be noise —
    # or, under a warnings-as-errors test run, a different exception
    # than the one this function documents.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        source = Image.open(io.BytesIO(raw))
    if source.width * source.height > MAX_UNDERLAY_PIXELS:
        raise ValueError(
            f"underlay picture is {source.width}x{source.height} pixels, over the "
            f"{UNDERLAY_MAX_SIDE_PX}x{UNDERLAY_MAX_SIDE_PX} limit"
        )
    picture = source.convert("RGBA")
    page = Image.new("RGBA", picture.size, DIAGRAM_BG)
    flattened = Image.alpha_composite(page, picture)
    faded = Image.blend(page, flattened, max(0.0, min(1.0, shape.opacity))).convert("RGB")
    if size is not None and size != faded.size:
        faded = faded.resize(size, Image.Resampling.LANCZOS)
    return faded
