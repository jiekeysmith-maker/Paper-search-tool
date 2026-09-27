from pathlib import Path

import pandas as pd
import pytest

import main as main_module
from src.assignment import (
    ASSIGNMENT_COLUMNS,
    ASSIGNMENT_PDF_URL_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    AssignmentValidationError,
    BATCH_ID_COLUMN,
    PAPER_ID_COLUMN,
    READ_TIER_COLUMN,
    SECONDARY_DECISION_COLUMN,
    VENUE_COLUMN,
    YEAR_COLUMN,
    validate_full_read_assignment,
)
from src.downloader import FULL_READ_MANIFEST_COLUMNS, download_secondary_candidates
from src.paths import ProjectPaths

# Superseded by test_kd_review_workflow.py; retained as a readable legacy
# FULL_READ/Read_Tier regression fixture rather than collected as the formal schema.
__test__ = False


class FakePdfResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"%PDF-1.4\nassignment\n%%EOF"


class TrackingSession:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return FakePdfResponse()


def _source_config(root: Path) -> Path:
    path = root / "source.yaml"
    path.write_text(
        "http:\n  timeout_seconds: 1\n  retries: 0\n  request_interval_seconds: 0\n",
        encoding="utf-8",
    )
    return path


def _full_row(paper_id: str, title: str, url: str) -> dict[str, str]:
    return {
        PAPER_ID_COLUMN: paper_id,
        "Title（标题）": title,
        "PDF_URL（官方PDF链接）": url,
        SECONDARY_DECISION_COLUMN: "FULL_READ",
    }


def _assignment_row(
    paper_id: str,
    title: str,
    url: str,
    tier: str = "Read_First",
    assistant: str = "Assistant_1",
    order: int = 1,
) -> dict[str, str]:
    return {
        PAPER_ID_COLUMN: paper_id,
        ASSIGNMENT_TITLE_COLUMN: title,
        YEAR_COLUMN: "2025",
        VENUE_COLUMN: "CVPR",
        ASSIGNMENT_PDF_URL_COLUMN: url,
        READ_TIER_COLUMN: tier,
        ASSISTANT_GROUP_COLUMN: assistant,
        ASSISTANT_ORDER_COLUMN: str(order),
        BATCH_ID_COLUMN: f"CVPR2025_{tier}_{assistant}",
    }


def _write_inputs(
    root: Path,
    full_rows: list[dict[str, str]],
    assignment_rows: list[dict[str, str]],
) -> ProjectPaths:
    paths = ProjectPaths(root, "CVPR", 2025)
    paths.full_read_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.full_read_assignment_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(full_rows).to_csv(paths.full_read_csv, index=False, encoding="utf-8-sig")
    pd.DataFrame(assignment_rows).reindex(columns=ASSIGNMENT_COLUMNS).to_csv(
        paths.full_read_assignment_csv,
        index=False,
        encoding="utf-8-sig",
    )
    return paths


def _two_valid_rows():
    full = [
        _full_row("P-FIRST", "First Tier Paper", "https://example.test/first.pdf"),
        _full_row("P-NORMAL", "Normal Tier Paper", "https://example.test/normal.pdf"),
    ]
    assignment = [
        _assignment_row(
            "P-FIRST",
            "First Tier Paper",
            "https://example.test/first.pdf",
            "Read_First",
            "Assistant_2",
            1,
        ),
        _assignment_row(
            "P-NORMAL",
            "Normal Tier Paper",
            "https://example.test/normal.pdf",
            "Read_Normal",
            "Assistant_4",
            1,
        ),
    ]
    return full, assignment


def test_v12_is_default_screen_rule():
    args = main_module.build_parser().parse_args(["screen", "--venue", "CVPR", "--year", "2025"])
    assert args.rules == Path("config/screening_rules_v1_2.yaml")


def test_valid_assignment_parses_and_matches_full_read(tmp_path):
    full, assignment = _two_valid_rows()
    paths = _write_inputs(tmp_path, full, assignment)
    result = validate_full_read_assignment(tmp_path, "CVPR", 2025)
    assert result.valid
    assert len(result.full_read) == len(result.assignment) == 2
    assert result.tier_counts == {"Read_First": 1, "Read_Normal": 1}
    assert paths.assignment_qc_report.exists()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "Missing Assignment"),
        ("extra", "Extra Assignment"),
        ("duplicate", "Duplicate Paper_ID in Assignment"),
        ("tier", "invalid Read_Tier"),
        ("assistant", "invalid Assistant_Group"),
        ("order", "invalid Assistant_Order"),
        ("batch", "empty Batch_ID"),
        ("title", "Title mismatch"),
        ("url", "missing PDF_URL"),
    ],
)
def test_invalid_assignment_fails_before_download(tmp_path, monkeypatch, mutation, expected):
    full, assignment = _two_valid_rows()
    if mutation == "missing":
        assignment.pop()
    elif mutation == "extra":
        assignment.append(_assignment_row("EXTRA", "Extra", "https://example.test/extra.pdf", order=2))
    elif mutation == "duplicate":
        assignment.append(dict(assignment[0]))
    elif mutation == "tier":
        assignment[0][READ_TIER_COLUMN] = "Read_Later"
    elif mutation == "assistant":
        assignment[0][ASSISTANT_GROUP_COLUMN] = "Assistant_6"
    elif mutation == "order":
        assignment[0][ASSISTANT_ORDER_COLUMN] = "0"
    elif mutation == "batch":
        assignment[0][BATCH_ID_COLUMN] = ""
    elif mutation == "title":
        assignment[0][ASSIGNMENT_TITLE_COLUMN] = "A Different Paper"
    elif mutation == "url":
        assignment[0][ASSIGNMENT_PDF_URL_COLUMN] = ""
    paths = _write_inputs(tmp_path, full, assignment)

    def forbidden_session(config):
        raise AssertionError("validation must fail before a download session is created")

    monkeypatch.setattr("src.downloader._secondary_session", forbidden_session)
    with pytest.raises(AssignmentValidationError):
        download_secondary_candidates(tmp_path, "CVPR", 2025, _source_config(tmp_path))
    qc = paths.assignment_qc_report.read_text(encoding="utf-8")
    assert qc.startswith("# WARNING")
    assert expected in qc
    assert not paths.full_read_pdf_dir.exists()


def test_assignment_download_goes_directly_to_final_tier_and_assistant_paths(tmp_path, monkeypatch):
    full, assignment = _two_valid_rows()
    paths = _write_inputs(tmp_path, full, assignment)
    session = TrackingSession()
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: session)

    manifest = download_secondary_candidates(
        tmp_path,
        "CVPR",
        2025,
        _source_config(tmp_path),
    )

    assert session.urls == ["https://example.test/first.pdf", "https://example.test/normal.pdf"]
    by_id = manifest.set_index(PAPER_ID_COLUMN)
    first = Path(by_id.loc["P-FIRST", "Local_PDF_Path（本地PDF路径）"])
    normal = Path(by_id.loc["P-NORMAL", "Local_PDF_Path（本地PDF路径）"])
    assert first == (paths.full_read_assistant_dir("Read_First", "Assistant_2") / "First Tier Paper.pdf").resolve()
    assert normal == (paths.full_read_assistant_dir("Read_Normal", "Assistant_4") / "Normal Tier Paper.pdf").resolve()
    assert not list(paths.full_read_pdf_dir.glob("*.pdf"))
    assert not paths.full_read_assistant_dir("Read_First", "Assistant_1").exists()
    assert not paths.full_read_assistant_dir("Read_Normal", "Assistant_1").exists()
    assert not paths.legacy_pdf_dir("RULE_KEEP").exists()
    assert not paths.legacy_pdf_dir("RULE_MAYBE").exists()
    assert list(manifest.columns) == FULL_READ_MANIFEST_COLUMNS
    for column in (
        READ_TIER_COLUMN,
        ASSISTANT_GROUP_COLUMN,
        ASSISTANT_ORDER_COLUMN,
        BATCH_ID_COLUMN,
        "Original_Title（原始标题）",
        "Saved_Filename（保存文件名）",
        "Local_PDF_Path（本地PDF路径）",
    ):
        assert column in manifest.columns
    report = paths.full_read_download_report.read_text(encoding="utf-8")
    assert "Expected: 2" in report and "Downloaded: 2" in report and "Status: OK" in report
    assert len(report.encode("utf-8")) < 2000


def test_sanitized_title_collision_is_recorded_and_never_overwrites(tmp_path, monkeypatch):
    full = [
        _full_row("P1", "A/B", "https://example.test/p1.pdf"),
        _full_row("P2", "A\\B", "https://example.test/p2.pdf"),
    ]
    assignment = [
        _assignment_row("P1", "A/B", "https://example.test/p1.pdf", assistant="Assistant_1"),
        _assignment_row("P2", "A\\B", "https://example.test/p2.pdf", assistant="Assistant_2"),
    ]
    _write_inputs(tmp_path, full, assignment)
    monkeypatch.setattr("src.downloader._secondary_session", lambda config: TrackingSession())

    manifest = download_secondary_candidates(tmp_path, "CVPR", 2025, _source_config(tmp_path))
    by_id = manifest.set_index(PAPER_ID_COLUMN)
    assert by_id.loc["P1", "Saved_Filename（保存文件名）"] == "A-B.pdf"
    assert by_id.loc["P1", "Filename_Collision（文件名冲突）"] == "FALSE"
    assert by_id.loc["P2", "Saved_Filename（保存文件名）"] == "A-B__P2.pdf"
    assert by_id.loc["P2", "Filename_Collision（文件名冲突）"] == "TRUE"
    assert Path(by_id.loc["P1", "Local_PDF_Path（本地PDF路径）"]).exists()
    assert Path(by_id.loc["P2", "Local_PDF_Path（本地PDF路径）"]).exists()


def test_balance_is_checked_separately_and_warning_never_reassigns(tmp_path):
    full_rows: list[dict[str, str]] = []
    assignment_rows: list[dict[str, str]] = []
    for tier in ("Read_First", "Read_Normal"):
        for index in range(1, 6):
            paper_id = f"{tier}-{index}"
            title = f"{tier} Paper {index}"
            url = f"https://example.test/{paper_id}.pdf"
            full_rows.append(_full_row(paper_id, title, url))
            assignment_rows.append(
                _assignment_row(paper_id, title, url, tier, f"Assistant_{index}", 1)
            )
    _write_inputs(tmp_path, full_rows, assignment_rows)
    balanced = validate_full_read_assignment(tmp_path, "CVPR", 2025)
    assert balanced.balanced == {"Read_First": True, "Read_Normal": True}

    for row in assignment_rows:
        if row[READ_TIER_COLUMN] == "Read_Normal":
            row[ASSISTANT_GROUP_COLUMN] = "Assistant_1"
            row[ASSISTANT_ORDER_COLUMN] = str(
                sum(
                    1
                    for candidate in assignment_rows
                    if candidate[READ_TIER_COLUMN] == "Read_Normal"
                    and candidate[ASSISTANT_GROUP_COLUMN] == "Assistant_1"
                )
            )
    _write_inputs(tmp_path, full_rows, assignment_rows)
    unbalanced = validate_full_read_assignment(tmp_path, "CVPR", 2025)
    assert unbalanced.valid
    assert unbalanced.balanced["Read_First"] is True
    assert unbalanced.balanced["Read_Normal"] is False
    assert "Read_Normal Assistant distribution is not balanced" in unbalanced.warnings
    assert set(unbalanced.assignment[ASSISTANT_GROUP_COLUMN].tail(5)) == {"Assistant_1"}


def test_validate_assignment_cli_runs_validation_only(tmp_path, monkeypatch):
    full, assignment = _two_valid_rows()
    _write_inputs(tmp_path, full, assignment)
    monkeypatch.setattr(
        main_module,
        "download_secondary_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not download")),
    )
    result = main_module.main(
        [
            "validate-assignment",
            "--venue",
            "CVPR",
            "--year",
            "2025",
            "--library-root",
            str(tmp_path),
        ]
    )
    assert result == 0
