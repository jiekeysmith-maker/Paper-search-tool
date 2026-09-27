"""Local, deterministic organization of FULL_READ PDFs from assignment CSV files."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .downloader import READ_FIRST, READ_ORDER_COLUMN, _valid_pdf, sanitize_filename
from .paths import ProjectPaths
from .utils import clean_cell, setup_logger, write_csv


ASSISTANT_COLUMN = "Assistant（助手）"
THEME_COLUMN = "Theme（主题）"
BATCH_ORDER_COLUMN = "Batch_Order（批次内序号）"
PAPER_ID_COLUMN = "Paper_ID（论文编号）"
TITLE_COLUMN = "Title（标题）"
ASSIGNMENT_LOCAL_PDF_COLUMN = "Local_PDF_Path（当前PDF路径）"
LOCAL_PDF_COLUMN = "Local_PDF_Path（本地PDF路径）"
TARGET_FOLDER_COLUMN = "Target_Folder（目标文件夹）"
DOWNLOAD_STATUS_COLUMN = "Download_Status（下载状态）"

LEGAL_ASSISTANTS = tuple(f"Assistant_{index}" for index in range(1, 6))
ASSIGNMENT_REQUIRED_COLUMNS = (
    ASSISTANT_COLUMN,
    THEME_COLUMN,
    BATCH_ORDER_COLUMN,
    PAPER_ID_COLUMN,
    TITLE_COLUMN,
    READ_ORDER_COLUMN,
    ASSIGNMENT_LOCAL_PDF_COLUMN,
    TARGET_FOLDER_COLUMN,
)
SUCCESS_DOWNLOAD_STATUSES = {"DOWNLOADED", "SKIPPED_EXISTS"}


def _read_assignment(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Read_First assignment CSV not found: {path}")
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing = [column for column in ASSIGNMENT_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Assignment CSV missing required columns: {', '.join(missing)}")

    validated = frame.copy()
    for column in ASSIGNMENT_REQUIRED_COLUMNS:
        validated[column] = validated[column].map(clean_cell).str.strip()

    empty_ids = validated.index[validated[PAPER_ID_COLUMN] == ""].tolist()
    if empty_ids:
        raise ValueError(
            f"Assignment CSV contains empty Paper_ID at data rows: {[index + 2 for index in empty_ids]}"
        )
    duplicates = sorted(
        validated.loc[validated[PAPER_ID_COLUMN].duplicated(keep=False), PAPER_ID_COLUMN].unique()
    )
    if duplicates:
        raise ValueError(f"Assignment CSV contains duplicate Paper_ID values: {', '.join(duplicates)}")

    invalid_assistants = sorted(
        value for value in validated[ASSISTANT_COLUMN].unique() if value not in LEGAL_ASSISTANTS
    )
    if invalid_assistants:
        raise ValueError(
            "Assignment CSV contains invalid Assistant values: "
            + ", ".join(repr(value) for value in invalid_assistants)
            + f"; allowed={list(LEGAL_ASSISTANTS)}"
        )
    invalid_orders = sorted(
        value for value in validated[READ_ORDER_COLUMN].unique() if value != READ_FIRST
    )
    if invalid_orders:
        raise ValueError(
            "Assignment CSV contains non-Read_First values: "
            + ", ".join(repr(value) for value in invalid_orders)
        )
    if len(validated) != 60:
        raise ValueError(f"Assignment CSV must contain exactly 60 rows; found {len(validated)}")
    assistant_counts = validated[ASSISTANT_COLUMN].value_counts().to_dict()
    wrong_counts = {
        assistant: int(assistant_counts.get(assistant, 0))
        for assistant in LEGAL_ASSISTANTS
        if int(assistant_counts.get(assistant, 0)) != 12
    }
    if wrong_counts:
        raise ValueError(f"Each Assistant must have exactly 12 papers; invalid counts={wrong_counts}")
    return validated


def _validate_target_folder(
    raw_value: str,
    assistant: str,
    paths: ProjectPaths,
    read_first_root: Path,
) -> Path:
    if not raw_value:
        raise ValueError(f"Assignment CSV contains empty Target_Folder for {assistant}")
    expected = paths.read_first_assistant_dir(assistant).resolve()
    normalized = raw_value.replace("\\", os.sep).replace("/", os.sep)
    raw_path = Path(normalized)
    candidates = {raw_path.resolve()} if raw_path.is_absolute() else {
        (paths.library_root / raw_path).resolve(),
        (paths.year_root / raw_path).resolve(),
        (paths.full_read_pdf_dir / raw_path).resolve(),
        (read_first_root / raw_path).resolve(),
    }
    if expected not in candidates:
        raise ValueError(
            f"Target_Folder does not resolve to {assistant}: value={raw_value!r}, expected={expected}"
        )
    return expected


def _read_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"FULL_READ PDF manifest not found: {path}")
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    required = {PAPER_ID_COLUMN, READ_ORDER_COLUMN, LOCAL_PDF_COLUMN, DOWNLOAD_STATUS_COLUMN}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"FULL_READ manifest is missing columns: {', '.join(missing)}")
    if frame[PAPER_ID_COLUMN].map(clean_cell).duplicated().any():
        raise ValueError("FULL_READ manifest contains duplicate Paper_ID values")
    return frame


def _paper_id_matches_filename(paper_id: str, path: Path) -> bool:
    safe_id = sanitize_filename(paper_id, 60).casefold()
    return path.name.casefold().startswith(safe_id + "_")


def organize_read_first_assignments(root: Path, venue: str, year: int) -> dict[str, int]:
    """Move all Read_First PDFs into Assistant folders after a complete preflight."""
    paths = ProjectPaths(root.resolve(), venue.upper(), year)
    assignment = _read_assignment(paths.read_first_assignment_csv)
    manifest_path = paths.full_read_manifest_csv
    manifest = _read_manifest(manifest_path)
    with manifest_path.open("r+b"):
        pass

    paths.ensure_stage("organize")
    logger = setup_logger(f"organize.read_first.{venue}.{year}", paths.organize_log)
    read_first_root = paths.full_read_order_dir("Read_First").resolve()
    read_normal_root = paths.full_read_order_dir("Read_Normal").resolve()
    if not read_first_root.is_dir():
        raise FileNotFoundError(f"Read_First PDF directory not found: {read_first_root}")

    assignment_ids = set(assignment[PAPER_ID_COLUMN])
    manifest_ids = manifest[PAPER_ID_COLUMN].map(clean_cell)
    manifest_read_first = manifest[manifest[READ_ORDER_COLUMN].map(clean_cell) == READ_FIRST]
    manifest_read_first_ids = set(manifest_read_first[PAPER_ID_COLUMN].map(clean_cell))
    if assignment_ids != manifest_read_first_ids:
        missing_from_assignment = sorted(manifest_read_first_ids - assignment_ids)
        missing_from_manifest = sorted(assignment_ids - manifest_read_first_ids)
        raise ValueError(
            "Assignment and Manifest Read_First Paper_ID sets differ; "
            f"missing_from_assignment={missing_from_assignment}, "
            f"missing_from_manifest={missing_from_manifest}"
        )
    bad_statuses = manifest_read_first.loc[
        ~manifest_read_first[DOWNLOAD_STATUS_COLUMN].map(clean_cell).isin(SUCCESS_DOWNLOAD_STATUSES),
        [PAPER_ID_COLUMN, DOWNLOAD_STATUS_COLUMN],
    ]
    if not bad_statuses.empty:
        details = ", ".join(
            f"{clean_cell(row[PAPER_ID_COLUMN])}={clean_cell(row[DOWNLOAD_STATUS_COLUMN])}"
            for row in bad_statuses.to_dict(orient="records")
        )
        raise ValueError(f"Read_First manifest contains non-success download statuses: {details}")

    all_pdfs = [path.resolve() for path in read_first_root.rglob("*.pdf")]
    if len(all_pdfs) != 60:
        raise ValueError(f"Read_First must contain exactly 60 PDFs before organization; found {len(all_pdfs)}")
    allowed_parents = {read_first_root, *((read_first_root / name).resolve() for name in LEGAL_ASSISTANTS)}
    unexpected_locations = sorted(
        (path for path in all_pdfs if path.parent not in allowed_parents),
        key=lambda item: str(item).casefold(),
    )
    if unexpected_locations:
        raise ValueError(
            "Read_First contains PDFs outside the root or legal Assistant folders: "
            + ", ".join(str(path) for path in unexpected_locations)
        )

    normal_snapshot = {
        str(path.resolve()): path.stat().st_size for path in read_normal_root.rglob("*.pdf")
    } if read_normal_root.exists() else {}
    plans: list[tuple[Path, Path, str]] = []
    assigned_sources: set[Path] = set()
    target_by_id: dict[str, Path] = {}
    already_correct = 0

    for row in assignment.to_dict(orient="records"):
        paper_id = clean_cell(row[PAPER_ID_COLUMN])
        assistant = clean_cell(row[ASSISTANT_COLUMN])
        target_folder = _validate_target_folder(
            clean_cell(row[TARGET_FOLDER_COLUMN]), assistant, paths, read_first_root
        )
        matches = [path for path in all_pdfs if _paper_id_matches_filename(paper_id, path)]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Cannot match Paper_ID {paper_id} to exactly one Read_First PDF; matches={len(matches)}"
            )
        source = matches[0]
        if not _valid_pdf(source):
            raise ValueError(f"Matched file is not a valid PDF for Paper_ID {paper_id}: {source}")
        target = (target_folder / source.name).resolve()
        try:
            source.relative_to(read_first_root)
            target.relative_to(read_first_root)
        except ValueError as exc:
            raise ValueError(f"PDF path escapes Read_First directory for Paper_ID {paper_id}") from exc
        if source in assigned_sources:
            raise ValueError(f"One PDF matched more than one Paper_ID: {source}")
        assigned_sources.add(source)
        target_by_id[paper_id] = target
        if source == target:
            already_correct += 1
        else:
            if target.exists():
                raise FileExistsError(
                    f"Target already exists for Paper_ID {paper_id}; refusing to create a duplicate: {target}"
                )
            plans.append((source, target, assistant))

    unmatched = set(all_pdfs) - assigned_sources
    if unmatched:
        raise ValueError(
            "Read_First contains PDFs not represented by the assignment: "
            + ", ".join(path.name for path in sorted(unmatched, key=lambda item: str(item).casefold()))
        )

    updated_manifest = manifest.copy()
    manifest_index_by_id = {
        clean_cell(paper_id): index for index, paper_id in manifest_ids.items()
    }
    for paper_id, target in target_by_id.items():
        updated_manifest.at[manifest_index_by_id[paper_id], LOCAL_PDF_COLUMN] = str(target)

    moved: list[tuple[Path, Path]] = []
    created_directories: set[Path] = set()
    temp_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    try:
        for source, target, _ in plans:
            if not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                created_directories.add(target.parent)
            source.replace(target)
            moved.append((source, target))

        assistant_counts = {
            assistant: len(list((read_first_root / assistant).glob("*.pdf")))
            for assistant in LEGAL_ASSISTANTS
        }
        direct_count = len(list(read_first_root.glob("*.pdf")))
        total_count = len(list(read_first_root.rglob("*.pdf")))
        if any(count != 12 for count in assistant_counts.values()):
            raise RuntimeError(f"Post-move Assistant PDF counts are invalid: {assistant_counts}")
        if direct_count != 0 or total_count != 60:
            raise RuntimeError(
                f"Post-move Read_First totals are invalid: direct={direct_count}, total={total_count}"
            )
        normal_after = {
            str(path.resolve()): path.stat().st_size for path in read_normal_root.rglob("*.pdf")
        } if read_normal_root.exists() else {}
        if normal_after != normal_snapshot:
            raise RuntimeError("Read_Normal changed during Read_First organization")

        write_csv(updated_manifest, temp_manifest, list(manifest.columns))
        temp_manifest.replace(manifest_path)
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
        for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        if temp_manifest.exists():
            temp_manifest.unlink()
        raise

    result = {
        **assistant_counts,
        "Moved_Total": len(plans),
        "Already_Correct": already_correct,
        "Read_First_Root": direct_count,
        "Read_First_Total": total_count,
    }
    logger.info("Organized Read_First assignments: %s", result)
    return result
