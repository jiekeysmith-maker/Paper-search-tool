from pathlib import Path

import pandas as pd
import pytest

import main as main_module
from src.downloader import (
    SECONDARY_DECISION_COLUMN,
    build_secondary_pdf_filename,
    download_pdf_file,
    download_secondary_candidates,
    load_full_read_csv,
)


class FakePdfResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\n"
        yield b"1 0 obj\n<<>>\nendobj\n%%EOF"


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return FakePdfResponse()


class FailIfCalledSession:
    def get(self, *args, **kwargs):
        raise AssertionError("network must not be called")


def _source_config(tmp_path: Path) -> Path:
    path = tmp_path / "source_config.yaml"
    path.write_text(
        "http:\n  user_agent: test-agent\n  timeout_seconds: 1\n  retries: 0\n  request_interval_seconds: 0\n",
        encoding="utf-8",
    )
    return path


def _full_read_path(root: Path) -> Path:
    return root / "output" / "CVPR_2026" / "secondary_screening" / "FULL_READ.csv"


def _write_full_read(root: Path, rows: list[dict[str, str]]) -> Path:
    path = _full_read_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "Paper_ID（论文编号）": "CVPR2026_MAIN_001",
        "Title（标题）": "A Valid FULL_READ Paper",
        "PDF_URL（官方PDF链接）": "https://example.test/paper.pdf",
        SECONDARY_DECISION_COLUMN: "FULL_READ",
        "Machine_Decision（机器一筛结果）": "RULE_KEEP（规则保留）",
    }
    row.update(overrides)
    return row


def test_pdf_download_and_existing_file_skip(tmp_path):
    destination = tmp_path / "paper.pdf"
    status, size = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert status == "DOWNLOADED"
    assert size == destination.stat().st_size
    status2, size2 = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert (status2, size2) == ("ALREADY_EXISTS", size)


def test_corrupt_existing_file_is_replaced(tmp_path):
    destination = tmp_path / "paper.pdf"
    destination.write_bytes(b"not a pdf")
    status, _ = download_pdf_file(FakeSession(), "https://example.test/paper.pdf", destination, timeout=1)
    assert status == "DOWNLOADED"
    assert destination.read_bytes().startswith(b"%PDF-")


def test_load_full_read_csv_by_column_name_and_download_only_to_full_read(tmp_path, monkeypatch):
    source_path = _write_full_read(tmp_path, [_valid_row()])
    reordered = pd.read_csv(source_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)[
        [
            "Machine_Decision（机器一筛结果）",
            "PDF_URL（官方PDF链接）",
            SECONDARY_DECISION_COLUMN,
            "Title（标题）",
            "Paper_ID（论文编号）",
        ]
    ]
    reordered.to_csv(source_path, index=False, encoding="utf-8-sig")
    loaded = load_full_read_csv(source_path)
    assert loaded.iloc[0]["Paper_ID（论文编号）"] == "CVPR2026_MAIN_001"

    session = FakeSession()
    monkeypatch.setattr("src.downloader._session", lambda config: session)
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
    )
    assert session.calls == 1
    assert list(manifest.columns) == [
        "Paper_ID（论文编号）",
        "Title（标题）",
        SECONDARY_DECISION_COLUMN,
        "PDF_URL（官方PDF链接）",
        "Download_Status（下载状态）",
        "Local_PDF_Path（本地PDF路径）",
        "File_Size_Bytes（文件大小_字节）",
        "Error（错误信息）",
    ]
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "DOWNLOADED"
    local_path = Path(manifest.iloc[0]["Local_PDF_Path（本地PDF路径）"])
    assert local_path.parent.name == "FULL_READ"
    assert not (tmp_path / "output" / "CVPR_2026" / "PDFs" / "RULE_KEEP" / local_path.name).exists()


@pytest.mark.parametrize("decision", ["RESERVE", "EXCLUDE"])
def test_secondary_rejects_reserve_and_exclude_before_download(tmp_path, monkeypatch, decision):
    _write_full_read(tmp_path, [_valid_row(**{SECONDARY_DECISION_COLUMN: decision})])
    monkeypatch.setattr("src.downloader._session", lambda config: FailIfCalledSession())
    with pytest.raises(ValueError, match="non-FULL_READ"):
        download_secondary_candidates(
            root=tmp_path,
            venue="CVPR",
            year=2026,
            source_config_path=_source_config(tmp_path),
        )
    assert not (tmp_path / "output" / "CVPR_2026" / "PDFs" / "FULL_READ").exists()


def test_secondary_missing_required_column_is_error(tmp_path):
    row = _valid_row()
    row.pop("PDF_URL（官方PDF链接）")
    path = _write_full_read(tmp_path, [row])
    with pytest.raises(ValueError, match="missing required columns"):
        load_full_read_csv(path)


def test_secondary_missing_input_file_is_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="FULL_READ.csv not found"):
        load_full_read_csv(_full_read_path(tmp_path))


def test_secondary_duplicate_paper_id_is_error(tmp_path):
    path = _write_full_read(
        tmp_path,
        [
            _valid_row(),
            _valid_row(**{"Title（标题）": "A Duplicate ID"}),
        ],
    )
    with pytest.raises(ValueError, match="duplicate Paper_ID"):
        load_full_read_csv(path)


def test_secondary_empty_pdf_url_is_error(tmp_path):
    path = _write_full_read(tmp_path, [_valid_row(**{"PDF_URL（官方PDF链接）": "  "})])
    with pytest.raises(ValueError, match="empty PDF_URL"):
        load_full_read_csv(path)


def test_secondary_existing_valid_pdf_is_skipped(tmp_path, monkeypatch):
    row = _valid_row()
    _write_full_read(tmp_path, [row])
    filename = build_secondary_pdf_filename(row["Paper_ID（论文编号）"], row["Title（标题）"])
    destination = tmp_path / "output" / "CVPR_2026" / "PDFs" / "FULL_READ" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4\nexisting\n%%EOF")
    monkeypatch.setattr("src.downloader._session", lambda config: FailIfCalledSession())
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
    )
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "SKIPPED_EXISTS"
    assert destination.read_bytes() == b"%PDF-1.4\nexisting\n%%EOF"


def test_secondary_invalid_existing_file_is_not_overwritten(tmp_path, monkeypatch):
    row = _valid_row()
    _write_full_read(tmp_path, [row])
    filename = build_secondary_pdf_filename(row["Paper_ID（论文编号）"], row["Title（标题）"])
    destination = tmp_path / "output" / "CVPR_2026" / "PDFs" / "FULL_READ" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"corrupt-existing-file")
    monkeypatch.setattr("src.downloader._session", lambda config: FailIfCalledSession())
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
    )
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "INVALID_EXISTING_FILE"
    assert "not overwritten" in manifest.iloc[0]["Error（错误信息）"]
    assert destination.read_bytes() == b"corrupt-existing-file"


def test_secondary_filename_is_windows_safe_and_readable():
    filename = build_secondary_pdf_filename("CVPR:2026/001", 'A:B<C>"D/E\\F|G?H*')
    assert filename.endswith(".pdf")
    assert filename.startswith("CVPR_2026_001_")
    assert not any(character in filename for character in '<>:"/\\|?*')
    assert len(filename) <= 180


def test_legacy_download_command_still_dispatches_to_original_downloader(monkeypatch):
    calls = []

    def fake_download_candidates(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(main_module, "download_candidates", fake_download_candidates)
    result = main_module.main(["download", "--venue", "CVPR", "--year", "2026", "--limit", "1"])
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["limit"] == 1


def test_download_secondary_command_dispatches_to_secondary_downloader(monkeypatch):
    calls = []

    def fake_download_secondary_candidates(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(main_module, "download_secondary_candidates", fake_download_secondary_candidates)
    result = main_module.main(
        ["download-secondary", "--venue", "CVPR", "--year", "2026", "--limit", "2"]
    )
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["limit"] == 2
