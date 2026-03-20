from __future__ import annotations

from io import BytesIO
from typing import BinaryIO


def extract_text_from_pdf(file_obj: BinaryIO) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency error
        raise RuntimeError("PDF parsing library is not available.") from exc

    reader = PdfReader(file_obj)
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_text_from_path(path: str) -> str:
    with open(path, "rb") as handle:
        return extract_text_from_pdf(handle)


def extract_text_from_bytes(data: bytes) -> str:
    return extract_text_from_pdf(BytesIO(data))
