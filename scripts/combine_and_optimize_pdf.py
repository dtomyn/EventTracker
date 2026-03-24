from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge all PDFs in a folder in alphabetical order and optimize output size."
        )
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Folder that contains PDF files.",
    )
    parser.add_argument(
        "output_name",
        nargs="?",
        default="BrucePowerSecurityClearanceFull.pdf",
        help="Name of the merged output PDF file.",
    )
    parser.add_argument(
        "--quality-mode",
        choices=["smallest", "balanced", "high"],
        default="smallest",
        help=(
            "Compression strategy: smallest aggressively shrinks, balanced keeps better legibility, "
            "high prioritizes readability with light compression."
        ),
    )
    return parser.parse_args()


def list_input_pdfs(source_dir: Path, output_path: Path) -> list[Path]:
    def sort_key(path: Path) -> tuple[int, int, str]:
        stem = path.stem.strip()
        match = re.match(r"^(\d+)", stem)
        if match:
            # Numeric prefixes like 01, 02, ..., 13 are sorted numerically first.
            return (0, int(match.group(1)), path.name.lower())
        return (1, 0, path.name.lower())

    pdfs = sorted(
        [
            p
            for p in source_dir.glob("*.pdf")
            if p.is_file() and p.resolve() != output_path.resolve()
        ],
        key=sort_key,
    )
    if not pdfs:
        raise SystemExit(f"No input PDF files found in: {source_dir}")
    return pdfs


def merge_pdfs(pdfs: list[Path], merged_path: Path) -> None:
    writer = PdfWriter()
    failed_files: list[tuple[Path, str]] = []

    for pdf in pdfs:
        try:
            reader = PdfReader(str(pdf))
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise ValueError("encrypted and requires a password")
            for page in reader.pages:
                writer.add_page(page)
        except Exception as exc:  # noqa: BLE001
            failed_files.append((pdf, str(exc)))

    if failed_files:
        lines = ["Could not read all PDFs:"]
        for file_path, reason in failed_files:
            lines.append(f"- {file_path.name}: {reason}")
        raise SystemExit("\n".join(lines))

    with merged_path.open("wb") as f:
        writer.write(f)


def compress_raster(src_path: Path, out_path: Path, dpi: int, quality: int) -> int:
    import fitz

    src = fitz.open(str(src_path))
    out = fitz.open()
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)

    for page in src:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = pix.tobytes("jpeg", jpg_quality=quality)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=img)

    out.save(str(out_path), garbage=4, deflate=True, clean=True)
    out.close()
    src.close()
    return out_path.stat().st_size


def main() -> int:
    args = parse_args()
    source_dir = args.source_dir
    output_path = source_dir / args.output_name
    quality_mode: str = args.quality_mode

    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Source directory not found or invalid: {source_dir}")

    pdfs = list_input_pdfs(source_dir, output_path)
    print("Processing order:")
    for idx, pdf in enumerate(pdfs, start=1):
        print(f"{idx:02d}. {pdf.name}")

    temp_merged = source_dir / "._tmp_merged_unoptimized.pdf"
    temp_best = source_dir / "._tmp_best_optimized.pdf"

    for tmp in (temp_merged, temp_best):
        if tmp.exists():
            tmp.unlink()

    merge_pdfs(pdfs, temp_merged)
    merged_size = temp_merged.stat().st_size

    best_size = merged_size
    shutil.copy2(temp_merged, temp_best)
    best_label = "original merged"

    if quality_mode == "high":
        compression_attempts = [
            (220, 90),
            (200, 88),
            (180, 85),
        ]
    elif quality_mode == "balanced":
        compression_attempts = [
            (180, 85),
            (160, 80),
            (150, 75),
            (140, 72),
            (130, 68),
        ]
    else:
        compression_attempts = [
            (150, 70),
            (130, 60),
            (120, 55),
            (110, 50),
            (100, 45),
            (90, 40),
            (80, 35),
            (72, 30),
            (66, 25),
        ]

    try:
        temp_candidate = source_dir / "._tmp_candidate.pdf"
        for dpi, quality in compression_attempts:
            if temp_candidate.exists():
                temp_candidate.unlink()
            candidate_size = compress_raster(
                src_path=temp_merged,
                out_path=temp_candidate,
                dpi=dpi,
                quality=quality,
            )
            if candidate_size < best_size:
                shutil.copy2(temp_candidate, temp_best)
                best_size = candidate_size
                best_label = f"raster dpi={dpi} quality={quality}"
        if temp_candidate.exists():
            temp_candidate.unlink()
    except ModuleNotFoundError:
        print("PyMuPDF not installed; keeping merged PDF without raster compression.")

    if output_path.exists():
        output_path.unlink()
    shutil.move(str(temp_best), str(output_path))

    if temp_merged.exists():
        temp_merged.unlink()

    print(f"Input PDFs merged: {len(pdfs)}")
    print(f"Output file: {output_path}")
    print(f"Output size bytes: {best_size}")
    print(f"Best strategy: {best_label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
