from __future__ import annotations

from pathlib import Path
from typing import Protocol


class OcrEngine(Protocol):
    def extract(self, media_path: str | Path) -> str: ...


class NullOcr:
    def extract(self, media_path: str | Path) -> str:
        return ""


class PytesseractOcr:
    """Optional local OCR adapter; credentials and Telegram data stay local."""

    def __init__(self, *, languages: str = "chi_sim+eng") -> None:
        self.languages = languages
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("Pillow and pytesseract are required for image OCR") from exc
        self._pytesseract = pytesseract
        self._image = Image

    def extract(self, media_path: str | Path) -> str:
        with self._image.open(media_path) as image:
            return str(self._pytesseract.image_to_string(image, lang=self.languages) or "").strip()
