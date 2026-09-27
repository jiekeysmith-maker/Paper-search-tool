from pathlib import Path

import main as main_module

from src.paths import DEFAULT_LIBRARY_ROOT, ProjectPaths


def test_default_library_root_is_canonical_windows_location():
    assert DEFAULT_LIBRARY_ROOT == Path(r"D:\_Knowledge Distillation\Paper Library")
    args = main_module.build_parser().parse_args(["report", "--year", "2026"])
    assert args.library_root == DEFAULT_LIBRARY_ROOT


def test_venue_year_paths_are_two_levels():
    root = Path("library")
    cvpr = ProjectPaths(root, "cvpr", 2026)
    aaai = ProjectPaths(root, "AAAI", 2025)
    assert cvpr.year_root == root / "CVPR" / "2026"
    assert aaai.year_root == root / "AAAI" / "2025"
    assert cvpr.raw_dir == root / "CVPR" / "2026" / "raw"
    assert cvpr.raw_csv == root / "CVPR" / "2026" / "raw" / "All_Papers.csv"
    assert cvpr.screening_csv == root / "CVPR" / "2026" / "screening" / "KD_Screening.csv"
    assert aaai.screening_dir == root / "AAAI" / "2025" / "screening"


def test_stage_directories_are_created_on_demand_without_legacy_pdf_folders(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    assert not paths.year_root.exists()

    paths.ensure_stage("screen")
    assert paths.screening_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert not paths.raw_dir.exists()
    assert not paths.pdf_dir.exists()
    assert not paths.reports_dir.exists()
    assert not paths.manifests_dir.exists()

    paths.ensure_stage("download-secondary")
    assert paths.manifests_dir.is_dir()
    assert paths.reports_dir.is_dir()
    assert not paths.pdf_dir.exists()
    assert not paths.full_read_pdf_dir.exists()
    assert not paths.kd_method_centric_pdf_dir.exists()
    assert not paths.other_kd_pdf_dir.exists()
    assert not paths.reserve_pdf_dir.exists()
    assert not paths.legacy_pdf_dir("RULE_KEEP").exists()
    assert not paths.legacy_pdf_dir("RULE_MAYBE").exists()


def test_full_read_and_reserve_paths_are_separate(tmp_path):
    paths = ProjectPaths(tmp_path, "ICLR", 2026)
    assert paths.full_read_pdf_dir == tmp_path / "ICLR" / "2026" / "PDFs" / "FULL_READ"
    assert paths.reserve_pdf_dir == tmp_path / "ICLR" / "2026" / "PDFs" / "RESERVE"
    assert paths.full_read_manifest_csv != paths.reserve_manifest_csv
    assert paths.full_read_csv != paths.reserve_csv
    assert paths.full_read_assignment_csv == (
        tmp_path / "ICLR" / "2026" / "assignments" / "ICLR2026_FULL_READ_Assignment.csv"
    )
    assert paths.full_read_assistant_dir("Read_First", "Assistant_3") == (
        tmp_path / "ICLR" / "2026" / "PDFs" / "FULL_READ" / "Read_First" / "Assistant_3"
    )
    assert paths.kd_review_assignment_csv == (
        tmp_path / "ICLR" / "2026" / "assignments" / "ICLR2026_KD_REVIEW_Assignment.csv"
    )
    assert paths.kd_review_manifest_csv.name == "ICLR2026_KD_REVIEW_PDF_Manifest.csv"
