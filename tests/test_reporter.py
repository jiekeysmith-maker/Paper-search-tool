from pathlib import Path

import pandas as pd

from src.downloader import SECONDARY_DECISION_COLUMN, SECONDARY_MANIFEST_COLUMNS
from src.paths import ProjectPaths
from src.status_reporter import generate_report
from src.utils import RAW_COLUMNS, SCREENING_COLUMNS


def _write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).reindex(columns=columns).to_csv(path, index=False, encoding="utf-8-sig")


def _raw_row(paper_id: str, status: str = "SUCCESS") -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": paper_id,
        "Title（标题）": f"Title {paper_id}",
        "Crawl_Status（抓取状态）": status,
    }


def _secondary_row(paper_id: str, title: str) -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": paper_id,
        "Title（标题）": title,
        "PDF_URL（官方PDF链接）": f"https://example.test/{paper_id}.pdf",
        SECONDARY_DECISION_COLUMN: "FULL_READ",
        "Read_Order（全文阅读顺序）": "Read_First（优先全文读）",
    }


def _manifest_row(paper_id: str, title: str, status: str, local_path: str = "") -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": paper_id,
        "Title（标题）": title,
        SECONDARY_DECISION_COLUMN: "FULL_READ",
        "Read_Order（全文阅读顺序）": "Read_First（优先全文读）",
        "PDF_URL（官方PDF链接）": f"https://example.test/{paper_id}.pdf",
        "Download_Status（下载状态）": status,
        "Local_PDF_Path（本地PDF路径）": local_path,
        "File_Size_Bytes（文件大小_字节）": "",
        "Error（错误信息）": "",
    }


def test_normal_report_is_compact_and_does_not_copy_paper_lists(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    raw = [_raw_row("P1"), _raw_row("P2")]
    _write_csv(paths.raw_csv, raw, RAW_COLUMNS)
    screening = [dict(row, **{"Rule_Version（规则版本）": "V1.2"}) for row in raw]
    _write_csv(paths.screening_csv, screening, SCREENING_COLUMNS)
    pool = [_secondary_row("P1", "Very Long Paper Title One"), _secondary_row("P2", "Very Long Paper Title Two")]
    _write_csv(paths.full_read_csv, pool, list(pool[0]))
    manifest = []
    for row in pool:
        pdf = paths.full_read_pdf_dir / f"{row['Paper_ID（论文编号）']}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4\nvalid\n%%EOF")
        manifest.append(_manifest_row(row["Paper_ID（论文编号）"], row["Title（标题）"], "DOWNLOADED", str(pdf)))
    _write_csv(paths.full_read_manifest_csv, manifest, SECONDARY_MANIFEST_COLUMNS)

    report_path = generate_report(tmp_path, "CVPR", 2026)
    text = report_path.read_text(encoding="utf-8")
    assert "Status: OK" in text
    assert "Rules Version: V1.2" in text
    assert "KD REVIEW PDF: 2/2" in text
    assert "Failed: 0" in text and "Missing: 0" in text and "Duplicate: 0" in text
    assert "Very Long Paper Title" not in text
    assert len(text.encode("utf-8")) < 2000


def test_warning_report_highlights_failures_missing_and_duplicates_without_logs(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2025)
    _write_csv(paths.raw_csv, [_raw_row("P1")], RAW_COLUMNS)
    duplicate_screening = [_raw_row("P1"), _raw_row("P1")]
    _write_csv(paths.screening_csv, duplicate_screening, SCREENING_COLUMNS)
    pool = [_secondary_row("P1", "Failed Candidate"), _secondary_row("P2", "Missing Candidate")]
    _write_csv(paths.full_read_csv, pool, list(pool[0]))
    _write_csv(
        paths.full_read_manifest_csv,
        [_manifest_row("P1", "Failed Candidate", "FAILED")],
        SECONDARY_MANIFEST_COLUMNS,
    )
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.logs_dir / "huge.log").write_text("TRACEBACK_MARKER\n" * 10000, encoding="utf-8")

    report_path = generate_report(tmp_path, "CVPR", 2025)
    text = report_path.read_text(encoding="utf-8")
    assert text.startswith("# WARNING")
    assert "Failed Candidate" in text and "Missing Candidate" in text
    assert "Failed: 1" in text and "Missing: 1" in text
    assert "Duplicate:" in text and "Status: WARNING" in text
    assert "TRACEBACK_MARKER" not in text
    assert len(text.encode("utf-8")) < 4000


def test_report_creates_only_report_and_log_directories_when_inputs_are_absent(tmp_path):
    paths = ProjectPaths(tmp_path, "AAAI", 2025)
    generate_report(tmp_path, "AAAI", 2025)
    assert paths.reports_dir.is_dir() and paths.logs_dir.is_dir()
    assert not paths.raw_dir.exists()
    assert not paths.screening_dir.exists()
    assert not paths.pdf_dir.exists()
    assert not paths.manifests_dir.exists()
