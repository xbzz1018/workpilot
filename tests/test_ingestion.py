from __future__ import annotations

from pathlib import Path

import pytest

import workpilot.ingestion as ingestion
from workpilot.ingestion import IngestionError, import_package


def test_imports_utf8_bom_text_markdown_and_quoted_csv(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# 标题\n完成上线\n", encoding="utf-8-sig")
    (tmp_path / "b.txt").write_text("关闭工单\n", encoding="utf-8")
    (tmp_path / "c.csv").write_text('task,note\nrelease,"含,逗号"\n', encoding="utf-8")
    package = import_package(tmp_path)
    assert len(package.materials) == 3
    assert len(package.evidence) == 4
    assert any(item.locator == "row:2" and "含,逗号" in item.excerpt for item in package.evidence)
    assert len({item.evidence_id for item in package.evidence}) == 4


@pytest.mark.parametrize("name", ["file.pdf", "image.png", "data.json"])
def test_rejects_unsupported_type(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("x", encoding="utf-8")
    with pytest.raises(IngestionError, match="unsupported"):
        import_package(tmp_path)


def test_rejects_invalid_encoding(tmp_path: Path) -> None:
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(IngestionError, match="UTF-8"):
        import_package(tmp_path)


def test_rejects_empty_and_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(IngestionError, match="empty"):
        import_package(tmp_path)
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    monkeypatch.setattr(ingestion, "MAX_FILE_BYTES", 4)
    with pytest.raises(IngestionError, match="1 MiB"):
        import_package(tmp_path)


def test_evidence_ids_are_stable(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("same line\n", encoding="utf-8")
    first = import_package(tmp_path)
    second = import_package(tmp_path)
    assert first == second


def test_rejects_empty_records_and_malformed_csv(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("\n \n", encoding="utf-8")
    with pytest.raises(IngestionError, match="no non-empty"):
        import_package(tmp_path)
    (tmp_path / "empty.txt").unlink()
    (tmp_path / "bad.csv").write_text("a,b\n1,2,3\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="more values"):
        import_package(tmp_path)


def test_rejects_duplicate_headers_and_long_record(tmp_path: Path) -> None:
    (tmp_path / "bad.csv").write_text("a,a\n1,2\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="duplicate"):
        import_package(tmp_path)
    (tmp_path / "bad.csv").unlink()
    (tmp_path / "long.txt").write_text("x" * 8001, encoding="utf-8")
    with pytest.raises(IngestionError, match="8000"):
        import_package(tmp_path)
