from pathlib import Path

import pandas as pd

from src.downloader import (
    SECONDARY_DECISION_COLUMN,
    SECONDARY_MANIFEST_COLUMNS,
    build_secondary_pdf_filename,
    download_reserve_candidates,
)
from src.paths import ProjectPaths


class FakePdfResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\nreserve\n%%EOF"


class TrackingSession:
    def __init__(self):
        self.urls = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
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


def _reserve_row(paper_id: str = "RESERVE_001", title: str = "Reserve Paper") -> dict[str, str]:
    return {
        "Paper_ID（论文编号）": paper_id,
        "Title（标题）": title,
        "PDF_URL（官方PDF链接）": f"https://example.test/{paper_id}.pdf",
        SECONDARY_DECISION_COLUMN: "RESERVE",
    }


def _write_reserve(root: Path, rows: list[dict[str, str]]) -> Path:
    path = ProjectPaths(root, "CVPR", 2026).reserve_csv
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _manifest_row(row: dict[str, str], status: str, local_path: str = "") -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": row["Paper_ID（论文编号）"],
        "Title（标题）": row["Title（标题）"],
        SECONDARY_DECISION_COLUMN: "RESERVE",
        "Read_Order（全文阅读顺序）": "",
        "PDF_URL（官方PDF链接）": row["PDF_URL（官方PDF链接）"],
        "Download_Status（下载状态）": status,
        "Local_PDF_Path（本地PDF路径）": local_path,
        "File_Size_Bytes（文件大小_字节）": "",
        "Error（错误信息）": "old failure" if status == "FAILED" else "",
    }


def test_download_reserve_reads_only_reserve_and_uses_independent_manifest(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    row = _reserve_row()
    _write_reserve(tmp_path, [row])
    paths.full_read_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.full_read_csv.write_text("this,is,not,a,valid,full-read-file", encoding="utf-8")
    session = TrackingSession()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)

    manifest = download_reserve_candidates(
        tmp_path, "CVPR", 2026, _source_config(tmp_path)
    )

    assert session.urls == [row["PDF_URL（官方PDF链接）"]]
    local_path = Path(manifest.iloc[0]["Local_PDF_Path（本地PDF路径）"])
    assert local_path.parent == paths.reserve_pdf_dir.resolve()
    assert paths.reserve_manifest_csv.exists()
    assert not paths.full_read_manifest_csv.exists()
    assert not paths.full_read_pdf_dir.exists()
    assert not paths.legacy_pdf_dir("RULE_KEEP").exists()
    assert not paths.legacy_pdf_dir("RULE_MAYBE").exists()


def test_reserve_retry_failed_requests_only_its_failed_manifest_rows(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    success = _reserve_row("RESERVE_OK", "Existing Reserve")
    failed = _reserve_row("RESERVE_FAILED", "Retry Reserve")
    _write_reserve(tmp_path, [success, failed])
    success_path = paths.reserve_pdf_dir / build_secondary_pdf_filename(
        success["Paper_ID（论文编号）"], success["Title（标题）"]
    )
    success_path.parent.mkdir(parents=True, exist_ok=True)
    success_path.write_bytes(b"%PDF-1.4\nexisting\n%%EOF")
    paths.manifests_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            _manifest_row(success, "DOWNLOADED", str(success_path.resolve())),
            _manifest_row(failed, "FAILED"),
        ]
    ).reindex(columns=SECONDARY_MANIFEST_COLUMNS).to_csv(
        paths.reserve_manifest_csv, index=False, encoding="utf-8-sig"
    )
    session = TrackingSession()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)

    manifest = download_reserve_candidates(
        tmp_path,
        "CVPR",
        2026,
        _source_config(tmp_path),
        retry_failed=True,
    )

    assert session.urls == [failed["PDF_URL（官方PDF链接）"]]
    by_id = manifest.set_index("Paper_ID（论文编号）")
    assert by_id.loc["RESERVE_OK", "Download_Status（下载状态）"] == "DOWNLOADED"
    assert by_id.loc["RESERVE_FAILED", "Download_Status（下载状态）"] == "DOWNLOADED"
    assert success_path.read_bytes() == b"%PDF-1.4\nexisting\n%%EOF"


def test_reserve_existing_valid_pdf_is_skipped_without_network(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    row = _reserve_row()
    _write_reserve(tmp_path, [row])
    destination = paths.reserve_pdf_dir / build_secondary_pdf_filename(
        row["Paper_ID（论文编号）"], row["Title（标题）"]
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4\nexisting\n%%EOF")
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: FailIfCalledSession())

    manifest = download_reserve_candidates(
        tmp_path, "CVPR", 2026, _source_config(tmp_path)
    )
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "SKIPPED_EXISTS"
