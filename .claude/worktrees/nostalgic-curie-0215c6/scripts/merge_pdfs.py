from __future__ import annotations

from pathlib import Path
import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge all PDFs in a folder in alphabetical order."
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Folder containing input PDF files.",
    )
    parser.add_argument(
        "output_file",
        type=Path,
        help="Output merged PDF path.",
    )
    parser.add_argument(
        "--password",
        default="",
        help="Optional password used for encrypted input PDFs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir: Path = args.source_dir
    output_file: Path = args.output_file
    password: str = args.password

    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Source folder does not exist or is not a directory: {source_dir}")

    pdf_files = sorted(
        [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
        key=lambda p: p.name.lower(),
    )

    if not pdf_files:
        raise SystemExit(f"No PDF files found in: {source_dir}")

    # Keep output deterministic and avoid self-including if rerun in-place.
    pdf_files = [p for p in pdf_files if p.resolve() != output_file.resolve()]

    if not pdf_files:
        raise SystemExit("No input PDFs left to merge after excluding output file.")

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    failed_files: list[tuple[Path, str]] = []
    for pdf in pdf_files:
        try:
            reader = PdfReader(str(pdf))
            if reader.is_encrypted:
                decrypt_result = reader.decrypt(password)
                if decrypt_result == 0:
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

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("wb") as f:
        writer.write(f)

    print(f"Merged {len(pdf_files)} files into: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
