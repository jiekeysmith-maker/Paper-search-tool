from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import main as main_module
from src.downloader import (
    READ_FIRST,
    READ_NORMAL,
    READ_ORDER_COLUMN,
    SECONDARY_DECISION_COLUMN,
    SECONDARY_MANIFEST_COLUMNS,
    build_secondary_pdf_filename,
)
from src.organizer import (
    ASSISTANT_COLUMN,
    ASSIGNMENT_LOCAL_PDF_COLUMN,
    ASSIGNMENT_REQUIRED_COLUMNS,
    BATCH_ORDER_COLUMN,
    DOWNLOAD_STATUS_COLUMN,
    LEGAL_ASSISTANTS,
    LOCAL_PDF_COLUMN,
    PAPER_ID_COLUMN,
    TARGET_FOLDER_COLUMN,
    THEME_COLUMN,
    TITLE_COLUMN,
    organize_read_first_assignments,
)
from src.paths import ProjectPaths


def _assignment_path(root: Path) -> Path:
    return ProjectPaths(root, "CVPR", 2026).read_first_assignment_csv


def _manifest_path(root: Path) -> Path:
    return ProjectPaths(root, "CVPR", 2026).full_read_manifest_csv


def _build_read_first_fixture(root: Path) -> dict[str, object]:
    paths = ProjectPaths(root, "CVPR", 2026)
    read_first = paths.full_read_order_dir("Read_First")
    read_normal = paths.full_read_order_dir("Read_Normal")
    read_first.mkdir(parents=True, exist_ok=True)
    read_normal.mkdir(parents=True, exist_ok=True)
    assignment_rows = []
    manifest_rows = []
    source_paths = []

    for index in range(60):
        assistant = LEGAL_ASSISTANTS[index // 12]
        paper_id = f"CVPR2026_MAIN_{index + 1:03d}"
        title = f"Assigned Paper {index + 1:03d}"
        source = read_first / build_secondary_pdf_filename(paper_id, title)
        source.write_bytes(f"%PDF-1.4\npaper {index + 1}\n%%EOF".encode("ascii"))
        source_paths.append(source)
        assignment_rows.append(
            {
                ASSISTANT_COLUMN: assistant,
                THEME_COLUMN: f"Theme {index // 12 + 1}",
                BATCH_ORDER_COLUMN: str(index % 12 + 1),
                PAPER_ID_COLUMN: paper_id,
                TITLE_COLUMN: title,
                READ_ORDER_COLUMN: READ_FIRST,
                ASSIGNMENT_LOCAL_PDF_COLUMN: str(source.resolve()),
                TARGET_FOLDER_COLUMN: str(
                    Path("CVPR")
                    / "2026"
                    / "PDFs"
                    / "FULL_READ"
                    / "Read_First"
                    / assistant
                ),
            }
        )
        manifest_rows.append(
            {
                PAPER_ID_COLUMN: paper_id,
                TITLE_COLUMN: title,
                SECONDARY_DECISION_COLUMN: "FULL_READ",
                READ_ORDER_COLUMN: READ_FIRST,
                "PDF_URL（官方PDF链接）": f"https://example.test/{paper_id}.pdf",
                DOWNLOAD_STATUS_COLUMN: "DOWNLOADED",
                LOCAL_PDF_COLUMN: str(source.resolve()),
                "File_Size_Bytes（文件大小_字节）": str(source.stat().st_size),
                "Error（错误信息）": "",
            }
        )

    normal_id = "CVPR2026_MAIN_NORMAL"
    normal_title = "Read Normal Must Stay Put"
    normal_path = read_normal / build_secondary_pdf_filename(normal_id, normal_title)
    normal_bytes = b"%PDF-1.4\nnormal untouched\n%%EOF"
    normal_path.write_bytes(normal_bytes)
    manifest_rows.append(
        {
            PAPER_ID_COLUMN: normal_id,
            TITLE_COLUMN: normal_title,
            SECONDARY_DECISION_COLUMN: "FULL_READ",
            READ_ORDER_COLUMN: READ_NORMAL,
            "PDF_URL（官方PDF链接）": "https://example.test/normal.pdf",
            DOWNLOAD_STATUS_COLUMN: "DOWNLOADED",
            LOCAL_PDF_COLUMN: str(normal_path.resolve()),
            "File_Size_Bytes（文件大小_字节）": str(normal_path.stat().st_size),
            "Error（错误信息）": "",
        }
    )

    assignment_path = _assignment_path(root)
    assignment_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(assignment_rows).reindex(columns=ASSIGNMENT_REQUIRED_COLUMNS).to_csv(
        assignment_path, index=False, encoding="utf-8-sig"
    )
    manifest_path = _manifest_path(root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(manifest_rows).reindex(columns=SECONDARY_MANIFEST_COLUMNS).to_csv(
        manifest_path, index=False, encoding="utf-8-sig"
    )
    return {
        "assignment_path": assignment_path,
        "manifest_path": manifest_path,
        "read_first": read_first,
        "normal_path": normal_path,
        "normal_bytes": normal_bytes,
        "source_paths": source_paths,
    }


def test_organize_read_first_moves_only_once_and_updates_manifest(tmp_path):
    fixture = _build_read_first_fixture(tmp_path)
    read_first = fixture["read_first"]

    first = organize_read_first_assignments(tmp_path, "CVPR", 2026)
    assert first["Moved_Total"] == 60
    assert first["Already_Correct"] == 0
    assert first["Read_First_Root"] == 0
    assert first["Read_First_Total"] == 60
    assert all(first[assistant] == 12 for assistant in LEGAL_ASSISTANTS)
    assert not list(read_first.glob("*.pdf"))
    assert all(not source.exists() for source in fixture["source_paths"])
    assert fixture["normal_path"].read_bytes() == fixture["normal_bytes"]

    organized = list(read_first.glob("Assistant_*/*.pdf"))
    assert len(organized) == 60
    assert len({path.resolve() for path in organized}) == 60
    manifest = pd.read_csv(
        fixture["manifest_path"], encoding="utf-8-sig", dtype=str, keep_default_na=False
    )
    read_first_rows = manifest[manifest[READ_ORDER_COLUMN] == READ_FIRST]
    assert len(read_first_rows) == 60
    assert all(Path(path).exists() for path in read_first_rows[LOCAL_PDF_COLUMN])
    assert {
        Path(path).parent.name for path in read_first_rows[LOCAL_PDF_COLUMN]
    } == set(LEGAL_ASSISTANTS)
    normal_manifest_path = Path(
        manifest.loc[manifest[READ_ORDER_COLUMN] == READ_NORMAL, LOCAL_PDF_COLUMN].iloc[0]
    )
    assert normal_manifest_path == fixture["normal_path"].resolve()

    second = organize_read_first_assignments(tmp_path, "CVPR", 2026)
    assert second["Moved_Total"] == 0
    assert second["Already_Correct"] == 60
    assert len(list(read_first.rglob("*.pdf"))) == 60


@pytest.mark.parametrize("failure", ["duplicate_id", "invalid_assistant", "invalid_target"])
def test_assignment_validation_stops_before_any_move(tmp_path, failure):
    fixture = _build_read_first_fixture(tmp_path)
    assignment = pd.read_csv(
        fixture["assignment_path"], encoding="utf-8-sig", dtype=str, keep_default_na=False
    )
    if failure == "duplicate_id":
        assignment.loc[1, PAPER_ID_COLUMN] = assignment.loc[0, PAPER_ID_COLUMN]
        expected = "duplicate Paper_ID"
    elif failure == "invalid_assistant":
        assignment.loc[0, ASSISTANT_COLUMN] = "Assistant_6"
        expected = "invalid Assistant"
    else:
        assignment.loc[0, TARGET_FOLDER_COLUMN] = "CVPR/2026/PDFs/FULL_READ/Read_Normal"
        expected = "Target_Folder"
    assignment.to_csv(fixture["assignment_path"], index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match=expected):
        organize_read_first_assignments(tmp_path, "CVPR", 2026)
    assert len(list(fixture["read_first"].glob("*.pdf"))) == 60
    assert not list(fixture["read_first"].glob("Assistant_*/*.pdf"))


def test_missing_paper_id_pdf_stops_before_any_move(tmp_path):
    fixture = _build_read_first_fixture(tmp_path)
    missing_source = fixture["source_paths"][0]
    unmatched = missing_source.with_name("UNASSIGNED_EXTRA_FILE.pdf")
    missing_source.replace(unmatched)

    with pytest.raises(FileNotFoundError, match="Cannot match Paper_ID"):
        organize_read_first_assignments(tmp_path, "CVPR", 2026)
    assert len(list(fixture["read_first"].glob("*.pdf"))) == 60
    assert not list(fixture["read_first"].glob("Assistant_*/*.pdf"))


def test_organize_read_first_command_dispatches(monkeypatch, tmp_path):
    calls = []

    def fake_organizer(root, venue, year):
        calls.append((root, venue, year))
        return {assistant: 12 for assistant in LEGAL_ASSISTANTS}

    monkeypatch.setattr(main_module, "organize_read_first_assignments", fake_organizer)
    result = main_module.main(
        [
            "organize-read-first",
            "--venue",
            "CVPR",
            "--year",
            "2026",
            "--library-root",
            str(tmp_path),
        ]
    )
    assert result == 0
    assert len(calls) == 1
    assert calls[0][1:] == ("CVPR", 2026)
