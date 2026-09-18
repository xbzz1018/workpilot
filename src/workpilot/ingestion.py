"""Deterministic, bounded ingestion for Markdown, TXT, and CSV work packages."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from workpilot.schemas import Evidence, ImportedPackage, Material

SUPPORTED_TYPES = {".md": "markdown", ".txt": "text", ".csv": "csv"}
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_PACKAGE_BYTES = 10 * 1024 * 1024


class IngestionError(ValueError):
    """Raised when a work package violates an import boundary."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{_digest(value.encode('utf-8'))[:16]}"


def _decode(raw: bytes, path: Path) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError(f"{path.name} is not valid UTF-8/UTF-8 BOM") from exc


def _text_records(text: str) -> list[tuple[str, str]]:
    return [(f"line:{number}", line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]


def _csv_records(text: str, path: Path) -> list[tuple[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if not reader.fieldnames or any(name is None or not name.strip() for name in reader.fieldnames):
            raise IngestionError(f"{path.name} must have a non-empty CSV header")
        normalized_headers = [name.strip() for name in reader.fieldnames]
        if len(normalized_headers) != len(set(normalized_headers)):
            raise IngestionError(f"{path.name} has duplicate CSV headers")
        records: list[tuple[str, str]] = []
        for number, row in enumerate(reader, 2):
            if None in row:
                raise IngestionError(f"{path.name} row {number} has more values than headers")
            excerpt = " | ".join(f"{key.strip()}={str(value or '').strip()}" for key, value in row.items())
            if any(str(value or "").strip() for value in row.values()):
                records.append((f"row:{number}", excerpt))
        return records
    except csv.Error as exc:
        raise IngestionError(f"{path.name} is not valid CSV: {exc}") from exc


def import_package(input_dir: str | Path) -> ImportedPackage:
    root = Path(input_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise IngestionError("input path must be a directory")

    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise IngestionError("work package is empty")

    materials: list[Material] = []
    evidence: list[Evidence] = []
    total_size = 0
    for path in files:
        if path.is_symlink():
            raise IngestionError(f"symbolic links are not allowed: {path.name}")
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise IngestionError(f"path escapes input directory: {path}") from exc
        media_type = SUPPORTED_TYPES.get(resolved.suffix.lower())
        if media_type is None:
            raise IngestionError(f"unsupported file type: {relative}")
        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            raise IngestionError(f"file exceeds 1 MiB limit: {relative}")
        total_size += size
        if total_size > MAX_PACKAGE_BYTES:
            raise IngestionError("work package exceeds 10 MiB limit")

        raw = resolved.read_bytes()
        text = _decode(raw, resolved)
        file_hash = _digest(raw)
        material_id = _stable_id("mat", f"{relative}\0{file_hash}")
        materials.append(Material(
            material_id=material_id,
            source_path=relative,
            media_type=media_type,
            sha256=file_hash,
            size_bytes=size,
        ))
        records = _csv_records(text, resolved) if media_type == "csv" else _text_records(text)
        for locator, excerpt in records:
            if len(excerpt) > 8000:
                raise IngestionError(f"record exceeds 8000 character limit: {relative} {locator}")
            evidence_hash = _digest(excerpt.encode("utf-8"))
            evidence.append(Evidence(
                evidence_id=_stable_id("ev", f"{material_id}\0{locator}\0{evidence_hash}"),
                material_id=material_id,
                source_path=relative,
                locator=locator,
                excerpt=excerpt,
                sha256=evidence_hash,
            ))

    if not evidence:
        raise IngestionError("work package contains no non-empty records")
    return ImportedPackage(materials=materials, evidence=evidence)
