from pathlib import Path

import pandas as pd
import pytest

from src.downloader import KD_REVIEW_MANIFEST_COLUMNS, download_secondary_candidates
from src.kd_assignment import (
    ASSIGNMENT_COLUMNS,
    ASSIGNMENT_PDF_URL_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    AssignmentValidationError,
    BATCH_ID_COLUMN,
    PAPER_ID_COLUMN,
    SECONDARY_CLASS_COLUMN,
    VENUE_COLUMN,
    YEAR_COLUMN,
    validate_kd_review_assignment,
)
from src.paths import ProjectPaths


class PdfResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\nreview\n%%EOF"


class Session:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return PdfResponse()


def _source_config(root: Path) -> Path:
    path = root / "source.yaml"
    path.write_text(
        "http:\n  timeout_seconds: 1\n  retries: 0\n  request_interval_seconds: 0\n",
        encoding="utf-8",
    )
    return path


def _pool_row(paper_id: str, title: str) -> dict[str, str]:
    return {
        PAPER_ID_COLUMN: paper_id,
        "Title（标题）": title,
        "PDF_URL（官方PDF链接）": f"https://example.test/{paper_id}.pdf",
    }


def _assignment_row(
    paper_id: str,
    title: str,
    secondary_class: str,
    assistant: str,
    order: int,
) -> dict[str, str]:
    return {
        PAPER_ID_COLUMN: paper_id,
        ASSIGNMENT_TITLE_COLUMN: title,
        YEAR_COLUMN: "2025",
        VENUE_COLUMN: "CVPR",
        ASSIGNMENT_PDF_URL_COLUMN: f"https://example.test/{paper_id}.pdf",
        SECONDARY_CLASS_COLUMN: secondary_class,
        ASSISTANT_GROUP_COLUMN: assistant,
        ASSISTANT_ORDER_COLUMN: str(order),
        BATCH_ID_COLUMN: f"CVPR2025_{secondary_class}_{assistant}",
    }


def _write_workflow(
    root: Path,
    method_rows: list[dict[str, str]],
    other_rows: list[dict[str, str]],
    assignment_rows: list[dict[str, str]],
    exclude_rows: list[dict[str, str]] | None = None,
) -> ProjectPaths:
    paths = ProjectPaths(root, "CVPR", 2025)
    paths.secondary_screening_dir.mkdir(parents=True, exist_ok=True)
    pool_columns = [PAPER_ID_COLUMN, "Title（标题）", "PDF_URL（官方PDF链接）"]
    pd.DataFrame(method_rows).reindex(columns=pool_columns).to_csv(
        paths.kd_method_centric_csv, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(other_rows).reindex(columns=pool_columns).to_csv(
        paths.other_kd_csv, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(exclude_rows or []).reindex(columns=pool_columns).to_csv(
        paths.exclude_csv, index=False, encoding="utf-8-sig"
    )
    paths.assignments_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(assignment_rows).reindex(columns=ASSIGNMENT_COLUMNS).to_csv(
        paths.kd_review_assignment_csv, index=False, encoding="utf-8-sig"
    )
    return paths


def _base_rows():
    method = [_pool_row("M1", "Method Paper")]
    other = [_pool_row("O1", "Other Paper")]
    assignment = [
        _assignment_row("M1", "Method Paper", "KD_Method_Centric", "Assistant_2", 1),
        _assignment_row("O1", "Other Paper", "Other_KD", "Assistant_4", 1),
    ]
    return method, other, assignment


def test_union_validation_excludes_exclude_pool(tmp_path):
    method, other, assignment = _base_rows()
    paths = _write_workflow(tmp_path, method, other, assignment, [_pool_row("X1", "Excluded")])
    result = validate_kd_review_assignment(tmp_path, "CVPR", 2025)
    assert result.valid
    assert set(result.secondary_review[PAPER_ID_COLUMN]) == {"M1", "O1"}
    assert "X1" not in set(result.assignment[PAPER_ID_COLUMN])
    assert paths.assignment_qc_report.exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "Missing Assignment"),
        ("extra", "Extra Assignment"),
        ("duplicate", "Duplicate Paper_ID in Assignment"),
        ("class", "invalid Secondary_Class"),
        ("assistant", "invalid Assistant_Group"),
    ],
)
def test_structural_assignment_errors_stop_before_network(tmp_path, monkeypatch, mutation, message):
    method, other, assignment = _base_rows()
    if mutation == "missing":
        assignment.pop()
    elif mutation == "extra":
        assignment.append(_assignment_row("X", "Extra", "Other_KD", "Assistant_1", 1))
    elif mutation == "duplicate":
        assignment.append(dict(assignment[0]))
    elif mutation == "class":
        assignment[0][SECONDARY_CLASS_COLUMN] = "FULL_READ"
    elif mutation == "assistant":
        assignment[0][ASSISTANT_GROUP_COLUMN] = "Assistant_6"
    paths = _write_workflow(tmp_path, method, other, assignment)
    monkeypatch.setattr(
        "src.downloader._secondary_session",
        lambda config: (_ for _ in ()).throw(AssertionError("network session must not be created")),
    )
    with pytest.raises(AssignmentValidationError):
        download_secondary_candidates(tmp_path, "CVPR", 2025, _source_config(tmp_path))
    assert message in paths.assignment_qc_report.read_text(encoding="utf-8")
    assert not paths.pdf_dir.exists()


def test_download_targets_new_classes_and_never_creates_legacy_pools(tmp_path, monkeypatch):
    method, other, assignment = _base_rows()
    paths = _write_workflow(tmp_path, method, other, assignment, [_pool_row("X1", "Excluded")])
    session = Session()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)
    manifest = download_secondary_candidates(tmp_path, "CVPR", 2025, _source_config(tmp_path))

    assert session.urls == ["https://example.test/M1.pdf", "https://example.test/O1.pdf"]
    by_id = manifest.set_index(PAPER_ID_COLUMN)
    method_path = Path(by_id.loc["M1", "Local_PDF_Path（本地PDF路径）"])
    other_path = Path(by_id.loc["O1", "Local_PDF_Path（本地PDF路径）"])
    assert method_path == (paths.kd_review_assistant_dir("KD_Method_Centric", "Assistant_2") / "Method Paper.pdf").resolve()
    assert other_path == (paths.kd_review_assistant_dir("Other_KD", "Assistant_4") / "Other Paper.pdf").resolve()
    assert set(manifest[SECONDARY_CLASS_COLUMN]) == {"KD_Method_Centric", "Other_KD"}
    assert list(manifest.columns) == KD_REVIEW_MANIFEST_COLUMNS
    assert not paths.full_read_pdf_dir.exists()
    assert not (paths.pdf_dir / "Read_First").exists()
    assert not (paths.pdf_dir / "Read_Normal").exists()
    assert not paths.reserve_pdf_dir.exists()
    assert not paths.legacy_pdf_dir("RULE_KEEP").exists()
    assert not paths.legacy_pdf_dir("RULE_MAYBE").exists()
    assert "X1" not in set(manifest[PAPER_ID_COLUMN])


def test_each_secondary_class_balance_is_checked_without_reassignment(tmp_path):
    method: list[dict[str, str]] = []
    other: list[dict[str, str]] = []
    assignment: list[dict[str, str]] = []
    for secondary_class, pool in (("KD_Method_Centric", method), ("Other_KD", other)):
        for index in range(1, 6):
            paper_id = f"{secondary_class}-{index}"
            title = f"{secondary_class} {index}"
            pool.append(_pool_row(paper_id, title))
            assignment.append(
                _assignment_row(paper_id, title, secondary_class, f"Assistant_{index}", 1)
            )
    _write_workflow(tmp_path, method, other, assignment)
    result = validate_kd_review_assignment(tmp_path, "CVPR", 2025)
    assert result.balanced == {"KD_Method_Centric": True, "Other_KD": True}

    normal_rows = [row for row in assignment if row[SECONDARY_CLASS_COLUMN] == "Other_KD"]
    for index, row in enumerate(normal_rows, start=1):
        row[ASSISTANT_GROUP_COLUMN] = "Assistant_1"
        row[ASSISTANT_ORDER_COLUMN] = str(index)
    _write_workflow(tmp_path, method, other, assignment)
    warning = validate_kd_review_assignment(tmp_path, "CVPR", 2025)
    assert warning.valid
    assert warning.balanced["KD_Method_Centric"] is True
    assert warning.balanced["Other_KD"] is False
    assert set(warning.assignment[warning.assignment[SECONDARY_CLASS_COLUMN] == "Other_KD"][ASSISTANT_GROUP_COLUMN]) == {"Assistant_1"}


def test_retry_failed_keeps_manifest_class_and_exact_target(tmp_path, monkeypatch):
    method, other, assignment = _base_rows()
    paths = _write_workflow(tmp_path, method, other, assignment)
    method_path = paths.kd_review_assistant_dir("KD_Method_Centric", "Assistant_2") / "Method Paper.pdf"
    other_path = paths.kd_review_assistant_dir("Other_KD", "Assistant_4") / "Other Paper.pdf"
    method_path.parent.mkdir(parents=True, exist_ok=True)
    method_path.write_bytes(b"%PDF-1.4\nexisting\n%%EOF")
    rows = [
        {
            PAPER_ID_COLUMN: "M1",
            "Title（标题）": "Method Paper",
            SECONDARY_CLASS_COLUMN: "KD_Method_Centric",
            ASSISTANT_GROUP_COLUMN: "Assistant_2",
            ASSISTANT_ORDER_COLUMN: "1",
            BATCH_ID_COLUMN: "CVPR2025_KD_Method_Centric_Assistant_2",
            "PDF_URL（官方PDF链接）": "https://example.test/M1.pdf",
            "Original_Title（原始标题）": "Method Paper",
            "Saved_Filename（保存文件名）": method_path.name,
            "Filename_Collision（文件名冲突）": "FALSE",
            "Local_PDF_Path（本地PDF路径）": str(method_path.resolve()),
            "Download_Status（下载状态）": "DOWNLOADED",
            "File_Size_Bytes（文件大小_字节）": method_path.stat().st_size,
            "Failure_Reason（失败原因）": "",
        },
        {
            PAPER_ID_COLUMN: "O1",
            "Title（标题）": "Other Paper",
            SECONDARY_CLASS_COLUMN: "Other_KD",
            ASSISTANT_GROUP_COLUMN: "Assistant_4",
            ASSISTANT_ORDER_COLUMN: "1",
            BATCH_ID_COLUMN: "CVPR2025_Other_KD_Assistant_4",
            "PDF_URL（官方PDF链接）": "https://example.test/O1.pdf",
            "Original_Title（原始标题）": "Other Paper",
            "Saved_Filename（保存文件名）": other_path.name,
            "Filename_Collision（文件名冲突）": "FALSE",
            "Local_PDF_Path（本地PDF路径）": str(other_path.resolve()),
            "Download_Status（下载状态）": "FAILED",
            "File_Size_Bytes（文件大小_字节）": "0",
            "Failure_Reason（失败原因）": "old failure",
        },
    ]
    paths.manifests_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).reindex(columns=KD_REVIEW_MANIFEST_COLUMNS).to_csv(
        paths.kd_review_manifest_csv, index=False, encoding="utf-8-sig"
    )
    session = Session()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)
    manifest = download_secondary_candidates(
        tmp_path, "CVPR", 2025, _source_config(tmp_path), retry_failed=True
    )
    assert session.urls == ["https://example.test/O1.pdf"]
    retried = manifest.set_index(PAPER_ID_COLUMN).loc["O1"]
    assert retried[SECONDARY_CLASS_COLUMN] == "Other_KD"
    assert Path(retried["Local_PDF_Path（本地PDF路径）"]) == other_path.resolve()

