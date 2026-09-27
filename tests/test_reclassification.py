from pathlib import Path

import pandas as pd
import pytest

from src.paths import ProjectPaths
from src.reclassification import (
    ReclassificationError,
    execute_reclassification,
    plan_reclassification,
)


def _row(
    root: Path,
    paper_id: str,
    title: str,
    secondary_class: str,
    assistant: str,
) -> dict[str, str]:
    return {
        "Paper_ID": paper_id,
        "Title": title,
        "Legacy_Read_Tier": "Read_First",
        "Final_KD_Centrality_Class": secondary_class,
        "Target_Category_Folder": secondary_class,
        "Target_Assistant_Folder": assistant,
        "Target_Directory": str(
            ProjectPaths(root, "CVPR", 2026).kd_review_assistant_dir(
                secondary_class, assistant
            ).resolve()
        ),
    }


def _fixture(root: Path):
    paths = ProjectPaths(root, "CVPR", 2026)
    legacy = paths.full_read_pdf_dir / "Read_First"
    legacy.mkdir(parents=True, exist_ok=True)
    first = legacy / "P1_Old Name.pdf"
    second = legacy / "P2_Old Other.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst\n%%EOF")
    second.write_bytes(b"%PDF-1.4\nsecond\n%%EOF")
    rows = [
        _row(root, "P1", "A: Method Paper", "KD_Method_Centric", "Assistant_1"),
        _row(root, "P2", "An Other Paper", "Other_KD", "Assistant_2"),
    ]
    csv_path = paths.assignments_dir / "CVPR2026_PDF_Reclassification.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    paths.manifests_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "Paper_ID（论文编号）": "P1",
                "PDF_URL（官方PDF链接）": "https://example.test/P1.pdf",
                "Local_PDF_Path（本地PDF路径）": str(first.resolve()),
            },
            {
                "Paper_ID（论文编号）": "P2",
                "PDF_URL（官方PDF链接）": "https://example.test/P2.pdf",
                "Local_PDF_Path（本地PDF路径）": str(second.resolve()),
            },
        ]
    ).to_csv(paths.full_read_manifest_csv, index=False, encoding="utf-8-sig")
    return paths, csv_path, first, second


def test_dry_run_is_read_only_and_ready(tmp_path):
    paths, csv_path, first, second = _fixture(tmp_path)
    plan = plan_reclassification(tmp_path, "CVPR", 2026, csv_path)
    assert plan.ready and plan.moved == 0
    assert first.exists() and second.exists()
    assert not paths.kd_method_centric_pdf_dir.exists()
    assert not paths.other_kd_pdf_dir.exists()
    assert "Status: READY" in paths.pdf_migration_report.read_text(encoding="utf-8")


def test_missing_source_stops(tmp_path):
    paths, csv_path, _, second = _fixture(tmp_path)
    second.unlink()
    with pytest.raises(ReclassificationError):
        plan_reclassification(tmp_path, "CVPR", 2026, csv_path)
    assert paths.full_read_pdf_dir.exists()
    assert not paths.kd_method_centric_pdf_dir.exists()


def test_ambiguous_source_stops(tmp_path):
    paths, csv_path, first, _ = _fixture(tmp_path)
    duplicate = first.with_name("duplicate_P1.pdf")
    duplicate.write_bytes(first.read_bytes())
    result = plan_reclassification(
        tmp_path, "CVPR", 2026, csv_path, raise_on_error=False
    )
    assert not result.ready and "P1" in result.ambiguous
    assert first.exists() and duplicate.exists()
    assert not paths.kd_method_centric_pdf_dir.exists()


def test_exact_legacy_title_match_normalizes_unicode_dash(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    legacy = paths.full_read_pdf_dir / "Read_Normal"
    legacy.mkdir(parents=True, exist_ok=True)
    source = legacy / "CVPR2026_MAIN_ABC123_Title Vision-Text.pdf"
    source.write_bytes(b"%PDF-1.4\ndash\n%%EOF")
    csv_path = paths.assignments_dir / "CVPR2026_PDF_Reclassification.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [_row(tmp_path, "SYNTHETIC_1", "Title Vision–Text", "KD_Method_Centric", "Assistant_1")]
    ).to_csv(csv_path, index=False, encoding="utf-8-sig")
    plan = plan_reclassification(tmp_path, "CVPR", 2026, csv_path)
    assert plan.ready and Path(plan.rows[0]["source"]) == source.resolve()


def test_target_collision_stops_before_move(tmp_path, monkeypatch):
    paths, csv_path, first, second = _fixture(tmp_path)

    def same_filename(title, paper_id, destination_dir, used_filenames):
        return "collision.pdf", False

    monkeypatch.setattr("src.reclassification.plan_title_pdf_filename", same_filename)
    frame = pd.read_csv(csv_path, encoding="utf-8-sig")
    frame.loc[1, "Target_Category_Folder"] = "KD_Method_Centric"
    frame.loc[1, "Final_KD_Centrality_Class"] = "KD_Method_Centric"
    frame.loc[1, "Target_Assistant_Folder"] = "Assistant_1"
    frame.loc[1, "Target_Directory"] = str(
        paths.kd_review_assistant_dir("KD_Method_Centric", "Assistant_1").resolve()
    )
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    result = plan_reclassification(
        tmp_path, "CVPR", 2026, csv_path, raise_on_error=False
    )
    assert not result.ready and result.collisions
    assert first.exists() and second.exists()


def test_execute_moves_renames_updates_manifest_and_removes_only_empty_legacy(tmp_path):
    paths, csv_path, first, second = _fixture(tmp_path)
    result = execute_reclassification(tmp_path, "CVPR", 2026, csv_path)
    method = paths.kd_review_assistant_dir("KD_Method_Centric", "Assistant_1") / "A- Method Paper.pdf"
    other = paths.kd_review_assistant_dir("Other_KD", "Assistant_2") / "An Other Paper.pdf"
    assert result.executed and result.moved == 2 and result.renamed == 2
    assert method.exists() and other.exists()
    assert not first.exists() and not second.exists()
    assert not paths.full_read_pdf_dir.exists()
    assert paths.kd_review_manifest_csv.exists()
    assert paths.manifests_dir.joinpath("CVPR2026_FULL_READ_PDF_Manifest_legacy.csv").exists()
    manifest = pd.read_csv(paths.kd_review_manifest_csv, encoding="utf-8-sig")
    assert set(manifest["Final_KD_Centrality_Class（最终KD核心性分类）"]) == {
        "KD_Method_Centric",
        "Other_KD",
    }
    assert set(manifest["Download_Status（下载状态）"]) == {"MIGRATED"}


def test_non_pdf_legacy_file_is_preserved_and_prevents_directory_deletion(tmp_path):
    paths, csv_path, first, second = _fixture(tmp_path)
    note = paths.full_read_pdf_dir / "do-not-delete.txt"
    note.write_text("keep", encoding="utf-8")
    result = execute_reclassification(tmp_path, "CVPR", 2026, csv_path)
    assert result.executed and result.preserved_legacy_files == 1
    assert note.exists() and not first.exists() and not second.exists()
    assert paths.full_read_pdf_dir.exists()
