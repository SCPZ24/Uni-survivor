#!/usr/bin/env python3
"""Extract unique image resources from a PowerPoint deck.

Usage:
    python3 extract_image.py path/of/pptx path/of/output

The default path handles .pptx/.pptm files directly by reading their OOXML zip
package. Legacy .ppt files are converted through LibreOffice/soffice when that
command is available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree


SUPPORTED_OOXML_EXTENSIONS = {".pptx", ".pptm", ".potx", ".potm"}
LEGACY_PPT_EXTENSION = ".ppt"
MEDIA_PREFIX = "ppt/media/"
MANIFEST_NAME = "manifest.json"

IMAGE_EXTENSIONS = {
    ".apng",
    ".bmp",
    ".dib",
    ".emf",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".jfif",
    ".jpe",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
    ".wmf",
}


class ExtractionError(Exception):
    """Raised when a deck cannot be processed."""


@dataclass
class ImageRecord:
    output_file: str
    sha256: str
    byte_size: int
    extension: str
    content_type: str | None
    sources: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract unique image resources from a PowerPoint deck."
    )
    parser.add_argument("deck", help="Path to a .pptx/.pptm deck, or .ppt if soffice is installed.")
    parser.add_argument("output", help="Directory where images and manifest.json are written.")
    return parser.parse_args(argv)


def natural_key(value: str) -> list[object]:
    parts: list[object] = []
    current = ""
    in_digits = False

    for char in value:
        char_is_digit = char.isdigit()
        if current and char_is_digit != in_digits:
            parts.append(int(current) if in_digits else current.lower())
            current = char
        else:
            current += char
        in_digits = char_is_digit

    if current:
        parts.append(int(current) if in_digits else current.lower())
    return parts


def load_content_types(package: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}

    try:
        raw_xml = package.read("[Content_Types].xml")
    except KeyError:
        return defaults, overrides

    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError:
        return defaults, overrides

    namespace = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    for child in root:
        if child.tag == f"{namespace}Default":
            extension = child.attrib.get("Extension")
            content_type = child.attrib.get("ContentType")
            if extension and content_type:
                defaults[extension.lower().lstrip(".")] = content_type
        elif child.tag == f"{namespace}Override":
            part_name = child.attrib.get("PartName")
            content_type = child.attrib.get("ContentType")
            if part_name and content_type:
                overrides[part_name.lstrip("/")] = content_type

    return defaults, overrides


def content_type_for(
    member_name: str,
    defaults: dict[str, str],
    overrides: dict[str, str],
) -> str | None:
    if member_name in overrides:
        return overrides[member_name]
    extension = Path(member_name).suffix.lower().lstrip(".")
    if not extension:
        return None
    return defaults.get(extension)


def is_image_member(
    member_name: str,
    content_type: str | None,
) -> bool:
    if not member_name.startswith(MEDIA_PREFIX) or member_name.endswith("/"):
        return False

    suffix = Path(member_name).suffix.lower()
    if content_type:
        normalized = content_type.lower()
        if normalized.startswith("image/") or normalized in {"application/x-msmetafile"}:
            return True
        if normalized.startswith(("video/", "audio/")):
            return False

    return suffix in IMAGE_EXTENSIONS


def choose_output_name(index: int, extension: str) -> str:
    safe_extension = extension.lower() if extension else ".bin"
    if safe_extension == ".jpeg":
        safe_extension = ".jpg"
    return f"image_{index:03d}{safe_extension}"


def extract_unique_images_from_ooxml(deck_path: Path, output_dir: Path) -> dict[str, object]:
    if not zipfile.is_zipfile(deck_path):
        raise ExtractionError(f"{deck_path} is not a readable OOXML PowerPoint package.")

    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(deck_path) as package:
        defaults, overrides = load_content_types(package)
        members = sorted(
            (
                item
                for item in package.infolist()
                if is_image_member(
                    item.filename,
                    content_type_for(item.filename, defaults, overrides),
                )
            ),
            key=lambda item: natural_key(item.filename),
        )

        records_by_hash: dict[str, ImageRecord] = {}
        ordered_hashes: list[str] = []

        for member in members:
            data = package.read(member)
            digest = hashlib.sha256(data).hexdigest()
            content_type = content_type_for(member.filename, defaults, overrides)

            if digest in records_by_hash:
                records_by_hash[digest].sources.append(member.filename)
                continue

            extension = Path(member.filename).suffix.lower()
            output_file = choose_output_name(len(ordered_hashes) + 1, extension)
            (output_dir / output_file).write_bytes(data)

            records_by_hash[digest] = ImageRecord(
                output_file=output_file,
                sha256=digest,
                byte_size=len(data),
                extension=extension,
                content_type=content_type,
                sources=[member.filename],
            )
            ordered_hashes.append(digest)

    records = [records_by_hash[digest] for digest in ordered_hashes]
    manifest: dict[str, object] = {
        "source": str(deck_path),
        "source_type": deck_path.suffix.lower().lstrip("."),
        "total_image_references": sum(len(record.sources) for record in records),
        "unique_image_count": len(records),
        "duplicate_image_references": sum(max(0, len(record.sources) - 1) for record in records),
        "images": [
            {
                "file": record.output_file,
                "sha256": record.sha256,
                "bytes": record.byte_size,
                "extension": record.extension,
                "content_type": record.content_type,
                "sources": record.sources,
            }
            for record in records
        ],
    }
    (output_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def find_soffice() -> str | None:
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def convert_legacy_ppt_to_pptx(deck_path: Path, work_dir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise ExtractionError(
            "Legacy .ppt input needs LibreOffice/soffice for conversion. "
            "Convert the file to .pptx first, then run this script again."
        )

    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pptx",
            "--outdir",
            str(work_dir),
            str(deck_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise ExtractionError(f"LibreOffice failed to convert {deck_path}: {details}")

    converted = work_dir / f"{deck_path.stem}.pptx"
    if not converted.exists():
        matches = sorted(work_dir.glob("*.pptx"))
        if not matches:
            raise ExtractionError(
                f"LibreOffice reported success, but no .pptx was created in {work_dir}."
            )
        converted = matches[0]
    return converted


def extract_images(deck_path: Path, output_dir: Path) -> dict[str, object]:
    if not deck_path.exists():
        raise ExtractionError(f"Input file does not exist: {deck_path}")
    if not deck_path.is_file():
        raise ExtractionError(f"Input path is not a file: {deck_path}")

    suffix = deck_path.suffix.lower()
    if suffix in SUPPORTED_OOXML_EXTENSIONS:
        return extract_unique_images_from_ooxml(deck_path, output_dir)

    if suffix == LEGACY_PPT_EXTENSION:
        with tempfile.TemporaryDirectory(prefix="ppt-image-extract-") as tmp:
            converted = convert_legacy_ppt_to_pptx(deck_path, Path(tmp))
            manifest = extract_unique_images_from_ooxml(converted, output_dir)
            manifest["source"] = str(deck_path)
            manifest["source_type"] = "ppt"
            manifest["converted_source"] = str(converted)
            (output_dir / MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return manifest

    supported = ", ".join(sorted(SUPPORTED_OOXML_EXTENSIONS | {LEGACY_PPT_EXTENSION}))
    raise ExtractionError(f"Unsupported file type {suffix!r}. Supported extensions: {supported}.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = extract_images(Path(args.deck), Path(args.output))
    except ExtractionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        "extracted "
        f"{manifest['unique_image_count']} unique image(s) "
        f"from {manifest['total_image_references']} image reference(s)"
    )
    print(f"manifest: {Path(args.output) / MANIFEST_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
