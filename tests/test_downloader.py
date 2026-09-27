from http.client import IncompleteRead
from pathlib import Path

import pandas as pd
import pytest
import requests

import main as main_module
from src.assignment import (
    ASSIGNMENT_PDF_URL_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
    READ_TIER_COLUMN,
    VENUE_COLUMN,
    YEAR_COLUMN,
)
from src.kd_assignment import (
    ASSIGNMENT_COLUMNS as KD_ASSIGNMENT_COLUMNS,
    ASSIGNMENT_PDF_URL_COLUMN as KD_ASSIGNMENT_PDF_URL_COLUMN,
    ASSIGNMENT_TITLE_COLUMN as KD_ASSIGNMENT_TITLE_COLUMN,
    ASSISTANT_GROUP_COLUMN as KD_ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN as KD_ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN as KD_BATCH_ID_COLUMN,
    SECONDARY_CLASS_COLUMN,
    VENUE_COLUMN as KD_VENUE_COLUMN,
    YEAR_COLUMN as KD_YEAR_COLUMN,
)
from src.downloader import (
    FULL_READ_MANIFEST_COLUMNS,
    READ_FIRST,
    READ_NORMAL,
    READ_ORDER_COLUMN,
    SECONDARY_DECISION_COLUMN,
    SECONDARY_MANIFEST_COLUMNS,
    build_secondary_pdf_filename,
    download_pdf_file,
    download_pdf_with_retries,
    download_reserve_candidates,
    download_secondary_candidates,
    load_full_read_csv,
    load_reserve_csv,
    organize_existing_secondary_pdfs,
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


class TrackingSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.urls = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return super().get(url, *args, **kwargs)


class AlwaysTimeoutSession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        raise requests.exceptions.ReadTimeout("temporary timeout")


class IncompletePdfResponse(FakePdfResponse):
    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\npartial"
        raise IncompleteRead(b"partial", 100)


class IncompleteSession:
    def get(self, *args, **kwargs):
        return IncompletePdfResponse()


class NonPdfResponse(FakePdfResponse):
    def iter_content(self, chunk_size):
        yield b"<!doctype html><title>not a pdf</title>"


class NonPdfSession:
    def get(self, *args, **kwargs):
        return NonPdfResponse()


def _source_config(tmp_path: Path) -> Path:
    path = tmp_path / "source_config.yaml"
    path.write_text(
        "http:\n  user_agent: test-agent\n  timeout_seconds: 1\n  retries: 0\n  request_interval_seconds: 0\n",
        encoding="utf-8",
    )
    return path


def _full_read_path(root: Path) -> Path:
    return ProjectPaths(root, "CVPR", 2026).full_read_csv


def _manifest_path(root: Path) -> Path:
    return ProjectPaths(root, "CVPR", 2026).full_read_manifest_csv


def _write_full_read(root: Path, rows: list[dict[str, str]]) -> Path:
    path = _full_read_path(root)
    paths = ProjectPaths(root, "CVPR", 2026)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    assignment_path = paths.full_read_assignment_csv
    assignment_path.parent.mkdir(parents=True, exist_ok=True)
    assignment_rows = []
    for index, row in enumerate(rows, start=1):
        read_order = row.get(READ_ORDER_COLUMN, READ_FIRST)
        read_tier = READ_ORDER_FOLDERS_FOR_TESTS.get(read_order, read_order)
        assignment_rows.append(
            {
                "Paper_ID（论文编号）": row.get("Paper_ID（论文编号）", ""),
                ASSIGNMENT_TITLE_COLUMN: row.get("Title（标题）", ""),
                YEAR_COLUMN: "2026",
                VENUE_COLUMN: "CVPR",
                ASSIGNMENT_PDF_URL_COLUMN: row.get("PDF_URL（官方PDF链接）", ""),
                READ_TIER_COLUMN: read_tier,
                ASSISTANT_GROUP_COLUMN: "Assistant_1",
                ASSISTANT_ORDER_COLUMN: str(index),
                BATCH_ID_COLUMN: f"CVPR2026_{read_tier}_Group1",
            }
        )
    pd.DataFrame(assignment_rows).to_csv(assignment_path, index=False, encoding="utf-8-sig")

    method_rows: list[dict[str, str]] = []
    other_rows: list[dict[str, str]] = []
    kd_assignments: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        decision = row.get(SECONDARY_DECISION_COLUMN, "")
        read_order = row.get(READ_ORDER_COLUMN, READ_FIRST)
        secondary_class = (
            "KD_Method_Centric"
            if read_order == READ_FIRST
            else "Other_KD"
            if read_order == READ_NORMAL
            else read_order
        )
        secondary_row = {
            "Paper_ID（论文编号）": row.get("Paper_ID（论文编号）", ""),
            "Title（标题）": row.get("Title（标题）", ""),
            "PDF_URL（官方PDF链接）": row.get("PDF_URL（官方PDF链接）", ""),
        }
        if decision == "FULL_READ":
            (method_rows if secondary_class == "KD_Method_Centric" else other_rows).append(secondary_row)
        kd_assignments.append(
            {
                "Paper_ID（论文编号）": row.get("Paper_ID（论文编号）", ""),
                KD_ASSIGNMENT_TITLE_COLUMN: row.get("Title（标题）", ""),
                KD_YEAR_COLUMN: "2026",
                KD_VENUE_COLUMN: "CVPR",
                KD_ASSIGNMENT_PDF_URL_COLUMN: row.get("PDF_URL（官方PDF链接）", ""),
                SECONDARY_CLASS_COLUMN: secondary_class,
                KD_ASSISTANT_GROUP_COLUMN: "Assistant_1",
                KD_ASSISTANT_ORDER_COLUMN: str(index),
                KD_BATCH_ID_COLUMN: f"CVPR2026_{secondary_class}_Group1",
            }
        )
    secondary_columns = ["Paper_ID（论文编号）", "Title（标题）", "PDF_URL（官方PDF链接）"]
    pd.DataFrame(method_rows).reindex(columns=secondary_columns).to_csv(
        paths.kd_method_centric_csv, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(other_rows).reindex(columns=secondary_columns).to_csv(
        paths.other_kd_csv, index=False, encoding="utf-8-sig"
    )
    paths.kd_review_assignment_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(kd_assignments).reindex(columns=KD_ASSIGNMENT_COLUMNS).to_csv(
        paths.kd_review_assignment_csv, index=False, encoding="utf-8-sig"
    )
    return path


READ_ORDER_FOLDERS_FOR_TESTS = {
    READ_FIRST: "Read_First",
    READ_NORMAL: "Read_Normal",
}


def _write_manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    path = _manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _write_kd_manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    path = ProjectPaths(root, "CVPR", 2026).kd_review_manifest_csv
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).reindex(columns=FULL_READ_MANIFEST_COLUMNS).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    return path


def _manifest_row(row: dict[str, str], status: str, local_path: str = "", error: str = "") -> dict[str, object]:
    return {
        "Paper_ID（论文编号）": row["Paper_ID（论文编号）"],
        "Title（标题）": row["Title（标题）"],
        SECONDARY_DECISION_COLUMN: "FULL_READ",
        "PDF_URL（官方PDF链接）": row["PDF_URL（官方PDF链接）"],
        "Download_Status（下载状态）": status,
        "Local_PDF_Path（本地PDF路径）": local_path,
        "File_Size_Bytes（文件大小_字节）": "",
        "Error（错误信息）": error,
    }


def _full_manifest_row(
    row: dict[str, str],
    status: str,
    local_path: str,
    secondary_class: str,
    assistant: str = "Assistant_1",
    error: str = "",
) -> dict[str, object]:
    filename = Path(local_path).name
    return {
        "Paper_ID（论文编号）": row["Paper_ID（论文编号）"],
        "Title（标题）": row["Title（标题）"],
        SECONDARY_CLASS_COLUMN: secondary_class,
        KD_ASSISTANT_GROUP_COLUMN: assistant,
        KD_ASSISTANT_ORDER_COLUMN: "1" if row["Paper_ID（论文编号）"].endswith("001") else "2",
        KD_BATCH_ID_COLUMN: f"CVPR2026_{secondary_class}_Group1",
        "PDF_URL（官方PDF链接）": row["PDF_URL（官方PDF链接）"],
        "Original_Title（原始标题）": row["Title（标题）"],
        "Saved_Filename（保存文件名）": filename,
        "Filename_Collision（文件名冲突）": "FALSE",
        "Local_PDF_Path（本地PDF路径）": local_path,
        "Download_Status（下载状态）": status,
        "File_Size_Bytes（文件大小_字节）": "",
        "Failure_Reason（失败原因）": error,
    }


def _valid_row(**overrides: str) -> dict[str, str]:
    row = {
        "Paper_ID（论文编号）": "CVPR2026_MAIN_001",
        "Title（标题）": "A Valid FULL_READ Paper",
        "PDF_URL（官方PDF链接）": "https://example.test/paper.pdf",
        SECONDARY_DECISION_COLUMN: "FULL_READ",
        READ_ORDER_COLUMN: READ_FIRST,
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
            READ_ORDER_COLUMN,
            "Title（标题）",
            "Paper_ID（论文编号）",
        ]
    ]
    reordered.to_csv(source_path, index=False, encoding="utf-8-sig")
    loaded = load_full_read_csv(source_path)
    assert loaded.iloc[0]["Paper_ID（论文编号）"] == "CVPR2026_MAIN_001"

    session = FakeSession()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
    )
    assert session.calls == 1
    assert list(manifest.columns) == FULL_READ_MANIFEST_COLUMNS
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "DOWNLOADED"
    local_path = Path(manifest.iloc[0]["Local_PDF_Path（本地PDF路径）"])
    assert local_path.name == "A Valid FULL_READ Paper.pdf"
    assert local_path.parent.name == "Assistant_1"
    assert local_path.parent.parent.name == "KD_Method_Centric"
    assert not ProjectPaths(tmp_path, "CVPR", 2026).full_read_pdf_dir.exists()
    assert not ProjectPaths(tmp_path, "CVPR", 2026).legacy_pdf_dir("RULE_KEEP").exists()


@pytest.mark.parametrize("decision", ["RESERVE", "EXCLUDE"])
def test_secondary_rejects_reserve_and_exclude_before_download(tmp_path, monkeypatch, decision):
    _write_full_read(tmp_path, [_valid_row(**{SECONDARY_DECISION_COLUMN: decision})])
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: FailIfCalledSession())
    with pytest.raises(ValueError, match="Assignment validation failed"):
        download_secondary_candidates(
            root=tmp_path,
            venue="CVPR",
            year=2026,
            source_config_path=_source_config(tmp_path),
        )
    assert not ProjectPaths(tmp_path, "CVPR", 2026).full_read_pdf_dir.exists()


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


def test_secondary_invalid_read_order_is_error_before_network(tmp_path, monkeypatch):
    _write_full_read(tmp_path, [_valid_row(**{READ_ORDER_COLUMN: "Read_Later"})])
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: FailIfCalledSession())
    with pytest.raises(ValueError, match="Assignment validation failed"):
        download_secondary_candidates(
            root=tmp_path,
            venue="CVPR",
            year=2026,
            source_config_path=_source_config(tmp_path),
        )


def test_secondary_existing_valid_pdf_is_skipped(tmp_path, monkeypatch):
    row = _valid_row()
    _write_full_read(tmp_path, [row])
    destination = (
        ProjectPaths(tmp_path, "CVPR", 2026).kd_review_assistant_dir("KD_Method_Centric", "Assistant_1")
        / f'{row["Title（标题）"]}.pdf'
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"%PDF-1.4\nexisting\n%%EOF")
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: FailIfCalledSession())
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
    destination = (
        ProjectPaths(tmp_path, "CVPR", 2026).kd_review_assistant_dir("KD_Method_Centric", "Assistant_1")
        / f'{row["Title（标题）"]}.pdf'
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"corrupt-existing-file")
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: FailIfCalledSession())
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
    )
    assert manifest.iloc[0]["Download_Status（下载状态）"] == "FAILED"
    assert "not overwritten" in manifest.iloc[0]["Failure_Reason（失败原因）"]
    assert destination.read_bytes() == b"corrupt-existing-file"


def test_organize_existing_pdfs_moves_without_copy_and_updates_manifest(tmp_path):
    first = _valid_row()
    normal = _valid_row(
        **{
            "Paper_ID（论文编号）": "CVPR2026_MAIN_002",
            "Title（标题）": "A Normal Read Paper",
            "PDF_URL（官方PDF链接）": "https://example.test/normal.pdf",
            READ_ORDER_COLUMN: READ_NORMAL,
        }
    )
    _write_full_read(tmp_path, [first, normal])
    flat_root = ProjectPaths(tmp_path, "CVPR", 2026).full_read_pdf_dir
    flat_root.mkdir(parents=True, exist_ok=True)
    first_source = flat_root / build_secondary_pdf_filename(first["Paper_ID（论文编号）"], first["Title（标题）"])
    normal_source = flat_root / build_secondary_pdf_filename(normal["Paper_ID（论文编号）"], normal["Title（标题）"])
    first_source.write_bytes(b"%PDF-1.4\nfirst\n%%EOF")
    normal_source.write_bytes(b"%PDF-1.4\nnormal\n%%EOF")
    _write_manifest(
        tmp_path,
        [
            _manifest_row(first, "DOWNLOADED", str(first_source.resolve())),
            _manifest_row(normal, "DOWNLOADED", str(normal_source.resolve())),
        ],
    )

    counts = organize_existing_secondary_pdfs(tmp_path, "CVPR", 2026)
    first_target = flat_root / "Read_First" / first_source.name
    normal_target = flat_root / "Read_Normal" / normal_source.name
    assert counts == {"Read_First": 1, "Read_Normal": 1, "Already_Organized": 0, "Moved_Total": 2}
    assert first_target.exists() and normal_target.exists()
    assert not first_source.exists() and not normal_source.exists()
    assert not list(flat_root.glob("*.pdf"))
    manifest = pd.read_csv(_manifest_path(tmp_path), encoding="utf-8-sig", dtype=str, keep_default_na=False)
    paths = set(manifest["Local_PDF_Path（本地PDF路径）"])
    assert paths == {str(first_target.resolve()), str(normal_target.resolve())}
    assert set(manifest[READ_ORDER_COLUMN]) == {READ_FIRST, READ_NORMAL}


def test_retry_failed_only_requests_failed_and_preserves_success(tmp_path, monkeypatch):
    success = _valid_row()
    failed = _valid_row(
        **{
            "Paper_ID（论文编号）": "CVPR2026_MAIN_009",
            "Title（标题）": "A Failed Paper",
            "PDF_URL（官方PDF链接）": "https://example.test/failed.pdf",
            READ_ORDER_COLUMN: READ_NORMAL,
        }
    )
    _write_full_read(tmp_path, [success, failed])
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    success_path = paths.kd_review_assistant_dir("KD_Method_Centric", "Assistant_1") / f'{success["Title（标题）"]}.pdf'
    failed_path = paths.kd_review_assistant_dir("Other_KD", "Assistant_1") / f'{failed["Title（标题）"]}.pdf'
    success_path.parent.mkdir(parents=True, exist_ok=True)
    success_path.write_bytes(b"%PDF-1.4\nsuccess\n%%EOF")
    _write_kd_manifest(
        tmp_path,
        [
            _full_manifest_row(success, "DOWNLOADED", str(success_path.resolve()), "KD_Method_Centric"),
            _full_manifest_row(
                failed,
                "FAILED",
                str(failed_path.resolve()),
                "Other_KD",
                error="ReadTimeout: old failure",
            ),
        ],
    )
    session = TrackingSession()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)
    manifest = download_secondary_candidates(
        root=tmp_path,
        venue="CVPR",
        year=2026,
        source_config_path=_source_config(tmp_path),
        retry_failed=True,
    )
    assert session.calls == 1
    assert session.urls == [failed["PDF_URL（官方PDF链接）"]]
    by_id = manifest.set_index("Paper_ID（论文编号）")
    assert by_id.loc[success["Paper_ID（论文编号）"], "Download_Status（下载状态）"] == "DOWNLOADED"
    assert by_id.loc[failed["Paper_ID（论文编号）"], "Download_Status（下载状态）"] == "DOWNLOADED"
    failed_downloaded_path = Path(by_id.loc[failed["Paper_ID（论文编号）"], "Local_PDF_Path（本地PDF路径）"])
    assert failed_downloaded_path == failed_path.resolve()
    assert failed_downloaded_path.parent.name == "Assistant_1"
    assert failed_downloaded_path.parent.parent.name == "Other_KD"
    assert success_path.read_bytes() == b"%PDF-1.4\nsuccess\n%%EOF"


def test_network_retries_are_bounded_with_expected_backoff(tmp_path):
    session = AlwaysTimeoutSession()
    delays = []
    destination = tmp_path / "retry.pdf"
    with pytest.raises(requests.exceptions.ReadTimeout):
        download_pdf_with_retries(
            session,
            "https://example.test/retry.pdf",
            destination,
            timeout=1,
            sleep_func=delays.append,
        )
    assert session.calls == 4
    assert delays == [2.0, 5.0, 10.0]
    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_incomplete_read_never_leaves_formal_or_part_pdf(tmp_path):
    destination = tmp_path / "incomplete.pdf"
    with pytest.raises(IncompleteRead):
        download_pdf_file(
            IncompleteSession(),
            "https://example.test/incomplete.pdf",
            destination,
            timeout=1,
        )
    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_non_pdf_response_fails_signature_check_without_leaving_files(tmp_path):
    destination = tmp_path / "not-pdf.pdf"
    with pytest.raises(ValueError, match="missing %PDF-"):
        download_pdf_file(
            NonPdfSession(),
            "https://example.test/not-pdf.pdf",
            destination,
            timeout=1,
        )
    assert not destination.exists()
    assert not destination.with_suffix(".pdf.part").exists()


def test_secondary_filename_is_windows_safe_and_readable():
    filename = build_secondary_pdf_filename("CVPR:2026/001", 'A:B<C>"D/E\\F|G?H*')
    assert filename.endswith(".pdf")
    assert filename.startswith("CVPR_2026_001_")
    assert not any(character in filename for character in '<>:"/\\|?*')
    assert len(filename) <= 180


def test_legacy_download_command_still_dispatches_to_original_downloader(monkeypatch, tmp_path):
    calls = []

    def fake_download_candidates(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(main_module, "download_candidates", fake_download_candidates)
    result = main_module.main(
        [
            "download",
            "--venue",
            "CVPR",
            "--year",
            "2026",
            "--limit",
            "1",
            "--library-root",
            str(tmp_path),
        ]
    )
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["limit"] == 1


def test_download_secondary_command_dispatches_to_secondary_downloader(monkeypatch, tmp_path):
    calls = []

    def fake_download_secondary_candidates(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(main_module, "download_secondary_candidates", fake_download_secondary_candidates)
    result = main_module.main(
        [
            "download-secondary",
            "--venue",
            "CVPR",
            "--year",
            "2026",
            "--limit",
            "2",
            "--library-root",
            str(tmp_path),
        ]
    )
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["limit"] == 2


def test_download_reserve_command_dispatches_and_retry_flag(monkeypatch, tmp_path):
    calls = []

    def fake_download_reserve_candidates(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(main_module, "download_reserve_candidates", fake_download_reserve_candidates)
    result = main_module.main(
        [
            "download-reserve",
            "--venue",
            "CVPR",
            "--year",
            "2026",
            "--retry-failed",
            "--library-root",
            str(tmp_path),
        ]
    )
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["root"] == tmp_path.resolve()
    assert calls[0]["retry_failed"] is True


def test_all_command_does_not_dispatch_legacy_download(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "crawl_cvf", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(main_module, "screen_papers", lambda *args, **kwargs: pd.DataFrame())

    def forbidden_legacy_download(**kwargs):
        raise AssertionError("all must not invoke legacy RULE_KEEP/RULE_MAYBE download")

    def forbidden_report(*args, **kwargs):
        raise AssertionError("all must stop at the secondary-screening gate")

    monkeypatch.setattr(main_module, "download_candidates", forbidden_legacy_download)
    monkeypatch.setattr(main_module, "generate_report", forbidden_report)
    result = main_module.main(
        ["all", "--venue", "CVPR", "--year", "2026", "--library-root", str(tmp_path)]
    )
    assert result == 0
