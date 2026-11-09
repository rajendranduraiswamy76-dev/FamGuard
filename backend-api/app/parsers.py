"""Upload validation (magic bytes, size, filename) and conversion into Vision images or text."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

import pypdfium2 as pdfium
from docx import Document as DocxDocument
from openpyxl import load_workbook
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024
MAX_PDF_PAGES: Final = 10
PDF_RENDER_DPI: Final = 150
MAX_IMAGE_SIDE: Final = 2048
MAX_IMAGE_PIXELS: Final = 60_000_000
MAX_XLSX_ROWS: Final = 5_000
MAX_EXTRACTED_CHARS: Final = 200_000
# Zip-bomb guard for DOCX/XLSX containers.
MAX_ZIP_UNCOMPRESSED_BYTES: Final = 100 * 1024 * 1024
MAX_ZIP_ENTRIES: Final = 2_000

_HEIF_BRANDS: Final = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
)
_FILENAME_ALLOWED = re.compile(r"[^A-Za-z0-9._ -]+")


class FileKind(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"
    HEIC = "heic"
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"

    @property
    def mime_type(self) -> str:
        return _MIME_TYPES[self]

    @property
    def is_image(self) -> bool:
        return self in (FileKind.JPEG, FileKind.PNG, FileKind.WEBP, FileKind.HEIC)


_MIME_TYPES: Final[dict[FileKind, str]] = {
    FileKind.JPEG: "image/jpeg",
    FileKind.PNG: "image/png",
    FileKind.WEBP: "image/webp",
    FileKind.HEIC: "image/heic",
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class UploadValidationError(Exception):
    pass


class EmptyFileError(UploadValidationError):
    pass


class FileTooLargeError(UploadValidationError):
    pass


class UnsupportedFileTypeError(UploadValidationError):
    pass


class FileParseError(Exception):
    """File type was valid but its contents could not be decoded."""


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    kind: FileKind
    filename: str
    size: int
    sha256: str

    @property
    def mime_type(self) -> str:
        return self.kind.mime_type


@dataclass(slots=True)
class PreparedInput:
    images: list[bytes] = field(default_factory=list)
    text: str | None = None


def sanitize_filename(name: str | None, *, max_length: int = 120) -> str:
    if not name:
        return "upload"
    base = re.split(r"[\\/]", name)[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _FILENAME_ALLOWED.sub("_", base).strip(" .")
    base = re.sub(r"_+", "_", base)
    if not base:
        return "upload"
    if len(base) > max_length:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            base = f"{stem[: max_length - len(ext) - 1]}.{ext}"
        else:
            base = base[:max_length]
    return base


def detect_file_kind(data: bytes) -> FileKind:
    if data.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return FileKind.WEBP
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return FileKind.HEIC
    if data.startswith(b"%PDF-"):
        return FileKind.PDF
    if data.startswith(b"PK\x03\x04"):
        return _detect_office_kind(data)
    raise UnsupportedFileTypeError("Unsupported or unrecognized file type")


def _detect_office_kind(data: bytes) -> FileKind:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or (
                sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED_BYTES
            ):
                raise UnsupportedFileTypeError("Archive expands beyond the allowed size")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile as exc:
        raise UnsupportedFileTypeError("Corrupt archive") from exc
    if "word/document.xml" in names:
        return FileKind.DOCX
    if "xl/workbook.xml" in names:
        return FileKind.XLSX
    raise UnsupportedFileTypeError("Unsupported archive type")


def validate_upload(data: bytes, filename: str | None) -> ValidatedUpload:
    if not data:
        raise EmptyFileError("File is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileTooLargeError(f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    kind = detect_file_kind(data)
    return ValidatedUpload(
        kind=kind,
        filename=sanitize_filename(filename),
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _normalize_image(image: Image.Image) -> bytes:
    """Upright, RGB, bounded size, re-encoded JPEG (drops EXIF incl. GPS)."""
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def _open_image(data: bytes) -> Image.Image:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data))
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise FileParseError("Image dimensions are too large")
            image.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise FileParseError("Image dimensions are too large") from exc
        except (OSError, SyntaxError, ValueError) as exc:
            raise FileParseError("Image could not be decoded") from exc
    return image


def image_to_jpeg(data: bytes) -> bytes:
    return _normalize_image(_open_image(data))


def pdf_to_images(data: bytes, *, max_pages: int = MAX_PDF_PAGES) -> list[bytes]:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise FileParseError("PDF could not be opened") from exc
    try:
        pages: list[bytes] = []
        for index in range(min(len(pdf), max_pages)):
            page = pdf[index]
            try:
                bitmap = page.render(scale=PDF_RENDER_DPI / 72)
                pages.append(_normalize_image(bitmap.to_pil()))
            finally:
                page.close()
        if not pages:
            raise FileParseError("PDF has no pages")
        return pages
    finally:
        pdf.close()


def docx_to_text(data: bytes) -> str:
    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types
        raise FileParseError("DOCX could not be opened") from exc
    lines = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return _cap("\n".join(lines))


def xlsx_to_text(data: bytes, *, max_rows: int = MAX_XLSX_ROWS) -> str:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types
        raise FileParseError("XLSX could not be opened") from exc
    lines: list[str] = []
    rows_seen = 0
    try:
        for sheet in workbook.worksheets:
            lines.append(f"# Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                if rows_seen >= max_rows:
                    break
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    lines.append(" | ".join(cells).rstrip(" |"))
                    rows_seen += 1
    finally:
        workbook.close()
    return _cap("\n".join(lines))


def prepare_for_extraction(data: bytes, kind: FileKind) -> PreparedInput:
    if kind.is_image:
        return PreparedInput(images=[image_to_jpeg(data)])
    if kind is FileKind.PDF:
        return PreparedInput(images=pdf_to_images(data))
    if kind is FileKind.DOCX:
        return PreparedInput(text=docx_to_text(data))
    if kind is FileKind.XLSX:
        return PreparedInput(text=xlsx_to_text(data))
    raise UnsupportedFileTypeError(f"No converter for {kind}")


def _cap(text: str) -> str:
    return text[:MAX_EXTRACTED_CHARS]
