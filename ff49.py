"""Официальное SIC -> Fama-French 49 industries mapping."""

from __future__ import annotations

import re
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "reference" / "Siccodes49.txt"
SOURCE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Siccodes49.zip"


def _ensure_source() -> Path:
    if CACHE.exists():
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SOURCE_URL) as response:
        archive = zipfile.ZipFile(BytesIO(response.read()))
        CACHE.write_bytes(archive.read("Siccodes49.txt"))
    return CACHE


def load_ranges() -> list[tuple[int, str, int, int]]:
    """Вернуть (номер, название, SIC_from, SIC_to) из первоисточника French."""
    current: tuple[int, str] | None = None
    ranges: list[tuple[int, str, int, int]] = []
    for line in _ensure_source().read_text(encoding="latin-1").splitlines():
        header = re.match(r"^\s*(\d+)\s+(\S+)\s+", line)
        if header:
            current = (int(header.group(1)), header.group(2))
            continue
        code_range = re.match(r"^\s*(\d{4})-(\d{4})\s+", line)
        if current and code_range:
            ranges.append((current[0], current[1], int(code_range.group(1)), int(code_range.group(2))))
    if not ranges:
        raise ValueError("Не удалось разобрать официальный FF49 SIC file")
    return ranges


def industry(sic: int | float | None, ranges: list[tuple[int, str, int, int]] | None = None) -> int | None:
    if sic is None:
        return None
    for number, _, first, last in ranges or load_ranges():
        if first <= int(sic) <= last:
            return number
    return None


if __name__ == "__main__":
    loaded = load_ranges()
    print(f"FF49 mapping: {len(loaded)} SIC ranges, {len(set(row[0] for row in loaded))} industries")
