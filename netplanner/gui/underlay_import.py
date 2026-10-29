"""Turn a PDF page or an image file into an underlay picture.

Lives in the GUI layer because both halves need Qt: QtPdf rasterizes
PDF pages (it ships inside the PyQt6 wheels, so this adds no Python
dependency), and QImageReader can read an image's dimensions from its
header and decode it straight to a smaller size. What leaves this
module is plain PNG bytes, which is all the core layers ever see.

Re-encoding to PNG is deliberate rather than incidental. It gives the
exporters one format to handle, and it drops whatever metadata the
source carried — a phone photo of a floor plan has the GPS position it
was taken at in its EXIF block, which has no business travelling inside
a plan file someone mails around.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QImage, QImageReader, QPainter

from netplanner.domain.entities import UNDERLAY_MAX_SIDE_PX
from netplanner.errors import UnderlayImportError

logger = logging.getLogger(__name__)

# What the file dialog offers. The image list is what QImageReader can
# decode in a stock PyQt6 install.
UNDERLAY_FILTER = (
    "Floor plans and images (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;"
    "PDF documents (*.pdf);;"
    "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;"
    "All files (*)"
)

# Architectural PDFs with embedded scans run to tens of megabytes; past
# this the file is almost certainly not a single drawing.
MAX_UNDERLAY_FILE_BYTES = 128 * 1024 * 1024

# The PNG that is stored in the plan. A floor plan — line art on white —
# compresses to a megabyte or two at full size; a photo at the same size
# can run to tens. Past this the picture is scaled down until it fits,
# so a few underlays never push an exported plan past the size the
# importer will accept back (project_file.MAX_PROJECT_BYTES).
MAX_UNDERLAY_PNG_BYTES = 12 * 1024 * 1024

# Resolution a PDF page is rasterized at, before the side cap applies.
PDF_RENDER_DPI = 200

# How large a fresh underlay appears on the canvas, along its longest
# side, in scene units. Source resolution says nothing useful about
# the drawing's scale relative to device cards (an A4 page is 842 pt, a
# phone photo 4000 px), so every import starts at one size that leaves
# room to place cards over individual rooms; resize from there.
DEFAULT_LONGEST_SIDE = 2400.0


@dataclass
class UnderlayPicture:
    """A decoded underlay, ready for AppController.add_underlay."""

    name: str
    png: bytes
    width: float   # initial size on the canvas, aspect ratio of the source
    height: float


def is_pdf(path: Path) -> bool:
    """Whether the file is a PDF, judged by its signature, not its name."""
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError as exc:
        raise UnderlayImportError(
            f"Could not read {path}: {type(exc).__name__}: {exc}"
        ) from exc


def load_underlay(
    path: Path, choose_page: Callable[[int], int | None]
) -> UnderlayPicture | None:
    """Read a PDF page or an image into an UnderlayPicture.

    `choose_page` is asked which page to use when a PDF has more than
    one; it receives the page count and returns a 0-based index, or None
    to cancel, in which case this returns None.

    Raises:
        UnderlayImportError: the file is unreadable, too large, not a
            PDF or a supported image, or password-protected.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UnderlayImportError(
            f"Could not read {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if size > MAX_UNDERLAY_FILE_BYTES:
        raise UnderlayImportError(
            f"{path.name} is {size} bytes, over the {MAX_UNDERLAY_FILE_BYTES}-byte "
            f"limit for an underlay"
        )

    if is_pdf(path):
        image = _render_pdf_page(path, choose_page)
        if image is None:
            return None
    else:
        image = _read_image(path)

    png = _encode_png(image)
    longest = max(image.width(), image.height())
    scale = DEFAULT_LONGEST_SIDE / longest
    logger.info(
        "Underlay %s decoded to %dx%d px, %d bytes of PNG",
        path.name, image.width(), image.height(), len(png),
    )
    return UnderlayPicture(
        name=path.name, png=png,
        width=image.width() * scale, height=image.height() * scale,
    )


def _fit(width: float, height: float, longest: int) -> QSize:
    """(width, height) scaled down, never up, so neither side passes `longest`."""
    factor = min(1.0, longest / max(width, height))
    return QSize(max(1, round(width * factor)), max(1, round(height * factor)))


def _render_pdf_page(path: Path, choose_page: Callable[[int], int | None]) -> QImage | None:
    """Rasterize one page of a PDF; None if the page choice was cancelled."""
    # Imported here rather than at the top: QtPdf links QtNetwork, which
    # needs Kerberos and Brotli libraries a minimal system may not have.
    # Imported at module level, a missing one would stop the whole app
    # from starting; here it costs only PDF import, with a message.
    try:
        from PyQt6.QtPdf import QPdfDocument
    except ImportError as exc:
        raise UnderlayImportError(
            f"PDF support is unavailable on this system ({exc}); install the "
            f"libraries listed in the README, or import the drawing as an image"
        ) from exc
    document = QPdfDocument(None)
    try:
        status = document.load(str(path))
        if status != QPdfDocument.Error.None_:
            reason = (
                "it is password-protected"
                if status == QPdfDocument.Error.IncorrectPassword
                else f"the PDF could not be opened ({status.name})"
            )
            raise UnderlayImportError(f"Could not use {path.name} as an underlay: {reason}")
        count = document.pageCount()
        page = 0
        if count > 1:
            chosen = choose_page(count)
            if chosen is None:
                return None
            page = chosen
        points = document.pagePointSize(page)
        target = _fit(
            points.width() * PDF_RENDER_DPI / 72,
            points.height() * PDF_RENDER_DPI / 72,
            UNDERLAY_MAX_SIDE_PX,
        )
        image = document.render(page, target)
    finally:
        document.close()
    if image.isNull():
        raise UnderlayImportError(f"Page {page + 1} of {path.name} could not be rendered")
    # Pages render onto transparency; give them the white sheet they
    # were drawn on, so the canvas and the exports agree on what shows.
    sheet = QImage(image.size(), QImage.Format.Format_RGB32)
    sheet.fill(Qt.GlobalColor.white)
    painter = QPainter(sheet)
    painter.drawImage(0, 0, image)
    painter.end()
    return sheet


def _read_image(path: Path) -> QImage:
    """Decode an image file, scaled down to the side cap if larger."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)  # honor EXIF rotation before it is stripped
    native = reader.size()  # header only; nothing decoded yet
    if not native.isValid():
        raise UnderlayImportError(
            f"{path.name} is not a PDF or an image format NetPlanner can read "
            f"({reader.errorString()})"
        )
    if max(native.width(), native.height()) > UNDERLAY_MAX_SIDE_PX:
        reader.setScaledSize(_fit(native.width(), native.height(), UNDERLAY_MAX_SIDE_PX))
    image = reader.read()
    if image.isNull():
        raise UnderlayImportError(f"Could not decode {path.name}: {reader.errorString()}")
    return image


def _encode_png(image: QImage) -> bytes:
    """PNG bytes for `image`, scaled down until under MAX_UNDERLAY_PNG_BYTES."""
    while True:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        data = bytes(buffer.data().data())
        if len(data) <= MAX_UNDERLAY_PNG_BYTES:
            return data
        logger.info(
            "Underlay PNG is %d bytes at %dx%d; scaling down",
            len(data), image.width(), image.height(),
        )
        image = image.scaled(
            _fit(image.width(), image.height(), int(max(image.width(), image.height()) * 0.75)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
