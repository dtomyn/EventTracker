from __future__ import annotations

from pathlib import Path

import fitz
from pypdf import PdfReader, PdfWriter


SOURCE_DIR = Path(r"C:/BrucePower/Sec")
MERGED_PATH = SOURCE_DIR / "BrucePowerSecurityClearanceFull.pdf"
COMPRESSED_PATH = SOURCE_DIR / "BrucePowerSecurityClearanceFull_under2MB.pdf"
TARGET_BYTES = 2 * 1024 * 1024


def merge_pdfs() -> None:
    pdfs = sorted(
        [p for p in SOURCE_DIR.glob("*.pdf") if p.name not in {MERGED_PATH.name, COMPRESSED_PATH.name}],
        key=lambda p: p.name.lower(),
    )
    if not pdfs:
        raise SystemExit("No PDFs found to merge.")

    writer = PdfWriter()
    for pdf in pdfs:
        reader = PdfReader(str(pdf))
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise SystemExit(f"Encrypted PDF requires password: {pdf.name}")
        for page in reader.pages:
            writer.add_page(page)

    with MERGED_PATH.open("wb") as f:
        writer.write(f)


def raster_compress(dpi: int, quality: int) -> int:
    src = fitz.open(str(MERGED_PATH))
    out = fitz.open()
    matrix = fitz.Matrix(dpi / 72, dpi / 72)

    for page in src:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = pix.tobytes("jpeg", jpg_quality=quality)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=img)

    out.save(str(COMPRESSED_PATH), garbage=4, deflate=True, clean=True)
    size = COMPRESSED_PATH.stat().st_size
    src.close()
    out.close()
    return size


def main() -> int:
    if not SOURCE_DIR.exists():
        raise SystemExit(f"Source directory not found: {SOURCE_DIR}")

    merge_pdfs()
    merged_size = MERGED_PATH.stat().st_size

    attempts = [
        (130, 60),
        (120, 55),
        (110, 50),
        (100, 45),
        (90, 40),
    ]

    compressed_size = 0
    chosen = None
    for dpi, quality in attempts:
        compressed_size = raster_compress(dpi=dpi, quality=quality)
        chosen = (dpi, quality)
        if compressed_size <= TARGET_BYTES:
            break

    print(f"Merged file: {MERGED_PATH} ({merged_size} bytes)")
    print(f"Compressed file: {COMPRESSED_PATH} ({compressed_size} bytes)")
    if chosen is not None:
        print(f"Settings used: dpi={chosen[0]}, jpeg_quality={chosen[1]}")
    print(f"Under 2 MB: {compressed_size <= TARGET_BYTES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
