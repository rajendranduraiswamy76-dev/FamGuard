from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from docx import Document as DocxDocument
from openpyxl import Workbook
from PIL import Image

from app.parsers import (
    MAX_UPLOAD_BYTES,
    EmptyFileError,
    FileKind,
    FileParseError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    detect_file_kind,
    prepare_for_extraction,
    sanitize_filename,
    validate_upload,
)


def _image_bytes(fmt: str, size: tuple[int, int] = (64, 48), color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _pdf_bytes(pages: int) -> bytes:
    images = [Image.new("RGB", (200, 260), c) for c in ("white", "blue", "green")[:pages]]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def _docx_bytes() -> bytes:
    doc = DocxDocument()
    doc.add_paragraph("Marriage certificate of Jane and John")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Date"
    table.rows[0].cells[1].text = "2015-06-20"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Bills"
    ws.append(["Vendor", "Amount"])
    ws.append(["Electric Co", 120.5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def samples() -> dict[str, bytes]:
    return {
        "jpeg": _image_bytes("JPEG"),
        "png": _image_bytes("PNG"),
        "webp": _image_bytes("WEBP"),
        "heic": _image_bytes("HEIF"),
        "pdf": _pdf_bytes(3),
        "docx": _docx_bytes(),
        "xlsx": _xlsx_bytes(),
    }


@pytest.mark.parametrize("kind", ["jpeg", "png", "webp", "heic", "pdf", "docx", "xlsx"])
def test_detects_supported_kinds(samples: dict[str, bytes], kind: str) -> None:
    assert detect_file_kind(samples[kind]) is FileKind(kind)


def test_spoofed_executable_rejected() -> None:
    exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(exe, "holiday.jpg")


def test_plain_zip_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_kind(buf.getvalue())


def test_zip_bomb_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (101 * 1024 * 1024))
    with pytest.raises(UnsupportedFileTypeError, match="allowed size"):
        detect_file_kind(buf.getvalue())


def test_size_and_empty_limits(samples: dict[str, bytes]) -> None:
    with pytest.raises(EmptyFileError):
        validate_upload(b"", "a.jpg")
    with pytest.raises(FileTooLargeError):
        validate_upload(samples["jpeg"] + b"\x00" * MAX_UPLOAD_BYTES, "a.jpg")


def test_validate_upload_hash_and_name(samples: dict[str, bytes]) -> None:
    result = validate_upload(samples["png"], "C:\\Users\\me\\My Scan (1).png")
    assert result.kind is FileKind.PNG
    assert result.mime_type == "image/png"
    assert result.filename == "My Scan _1_.png"
    assert result.sha256 == hashlib.sha256(samples["png"]).hexdigest()
    assert validate_upload(samples["png"], "x").sha256 == result.sha256


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("a\x00b\r\n.pdf", "ab.pdf"),
        ("", "upload"),
        (None, "upload"),
        ("...", "upload"),
        ("<script>.jpg", "_script_.jpg"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_keeping_extension() -> None:
    name = sanitize_filename("x" * 300 + ".pdf")
    assert len(name) == 120 and name.endswith(".pdf")


@pytest.mark.parametrize("kind", ["jpeg", "png", "webp", "heic"])
def test_images_normalized_to_jpeg(samples: dict[str, bytes], kind: str) -> None:
    prepared = prepare_for_extraction(samples[kind], FileKind(kind))
    assert prepared.text is None and len(prepared.images) == 1
    assert prepared.images[0].startswith(b"\xff\xd8\xff")


def test_large_image_downscaled() -> None:
    prepared = prepare_for_extraction(_image_bytes("PNG", (4000, 1000)), FileKind.PNG)
    with Image.open(io.BytesIO(prepared.images[0])) as img:
        assert max(img.size) == 2048


def test_pdf_pages_rendered(samples: dict[str, bytes]) -> None:
    prepared = prepare_for_extraction(samples["pdf"], FileKind.PDF)
    assert len(prepared.images) == 3
    assert all(p.startswith(b"\xff\xd8\xff") for p in prepared.images)


def test_docx_text(samples: dict[str, bytes]) -> None:
    text = prepare_for_extraction(samples["docx"], FileKind.DOCX).text
    assert text is not None
    assert "Marriage certificate of Jane and John" in text
    assert "Date | 2015-06-20" in text


def test_xlsx_text(samples: dict[str, bytes]) -> None:
    text = prepare_for_extraction(samples["xlsx"], FileKind.XLSX).text
    assert text == "# Sheet: Bills\nVendor | Amount\nElectric Co | 120.5"


def test_corrupt_image_raises_parse_error() -> None:
    with pytest.raises(FileParseError):
        prepare_for_extraction(b"\xff\xd8\xff" + b"garbage" * 10, FileKind.JPEG)


def test_corrupt_pdf_raises_parse_error() -> None:
    with pytest.raises(FileParseError):
        prepare_for_extraction(b"%PDF-1.4 broken", FileKind.PDF)
