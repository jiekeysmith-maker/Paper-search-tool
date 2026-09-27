"""Safe dry-run and transactional PDF reclassification for one Venue-Year."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .downloader import KD_REVIEW_MANIFEST_COLUMNS, _valid_pdf, sanitize_filename
from .kd_assignment import (
    ASSISTANT_GROUPS,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
    PAPER_ID_COLUMN,
    SECONDARY_CLASS_COLUMN,
)
from .paths import ProjectPaths
from .pdf_naming import plan_title_pdf_filename, sanitize_pdf_filename
from .utils import clean_cell, write_csv


CSV_PAPER_ID = "Paper_ID"
CSV_TITLE = "Title"
CSV_FINAL_CLASS = "Final_KD_Centrality_Class"
CSV_TARGET_CATEGORY = "Target_Category_Folder"
CSV_TARGET_ASSISTANT = "Target_Assistant_Folder"
CSV_TARGET_DIRECTORY = "Target_Directory"
CSV_LEGACY_TIER = "Legacy_Read_Tier"
REQUIRED_COLUMNS = (
    CSV_PAPER_ID,
    CSV_TITLE,
    CSV_FINAL_CLASS,
    CSV_TARGET_CATEGORY,
    CSV_TARGET_ASSISTANT,
    CSV_TARGET_DIRECTORY,
)
CLASS_ALIASES = {
    "KD_Method_Centric": "KD_Method_Centric",
    "KD Method-Centric（KD方法核心创新）": "KD_Method_Centric",
    "Other_KD": "Other_KD",
    "Other KD（其他KD论文）": "Other_KD",
    "Other（其他论文）": "Other_KD",
}
MIGRATION_MANIFEST_COLUMNS = [
    *KD_REVIEW_MANIFEST_COLUMNS,
    "Final_KD_Centrality_Class（最终KD核心性分类）",
    "Legacy_Read_Tier（历史阅读层级）",
]
MAX_REPORT_DETAILS = 20


class ReclassificationError(ValueError):
    """Raised when dry-run is not READY or execute cannot complete safely."""


@dataclass
class ReclassificationPlan:
    rows: list[dict[str, object]]
    expected_count: int
    errors: list[str]
    missing: list[str]
    ambiguous: list[str]
    collisions: list[str]
    class_counts: dict[str, int]
    inventory_count: int
    preserved_legacy_files: int
    report_path: Path
    ready: bool
    executed: bool = False
    moved: int = 0
    renamed: int = 0
    legacy_full_read_exists: bool = False


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_manifest_records(paths: ProjectPaths) -> tuple[dict[str, dict[str, str]], Path | None]:
    for manifest_path in (paths.kd_review_manifest_csv, paths.full_read_manifest_csv):
        if not manifest_path.exists():
            continue
        frame = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        id_column = PAPER_ID_COLUMN if PAPER_ID_COLUMN in frame.columns else CSV_PAPER_ID
        if id_column not in frame.columns:
            continue
        records = {
            clean_cell(row[id_column]).strip(): row
            for row in frame.to_dict(orient="records")
            if clean_cell(row[id_column]).strip()
        }
        return records, manifest_path
    return {}, None


def _manifest_value(record: dict[str, object], *names: str) -> str:
    for name in names:
        value = clean_cell(record.get(name)).strip()
        if value:
            return value
    return ""


def _resolve_source(
    paper_id: str,
    title: str,
    inventory: list[Path],
    manifest_record: dict[str, object],
    pdf_root: Path,
) -> list[Path]:
    """Collect only exact, auditable matches in the documented priority order."""
    candidates: set[Path] = set()
    recorded = _manifest_value(manifest_record, "Local_PDF_Path（本地PDF路径）", "Local_PDF_Path")
    if recorded:
        recorded_path = Path(recorded).resolve()
        if recorded_path.exists() and _inside(recorded_path, pdf_root):
            candidates.add(recorded_path)

    paper_key = paper_id.casefold()
    candidates.update(path for path in inventory if paper_key in path.name.casefold())

    if recorded:
        recorded_name = Path(recorded).name.casefold()
        candidates.update(path for path in inventory if path.name.casefold() == recorded_name)

    title_name = f"{sanitize_pdf_filename(title)}.pdf"
    candidates.update(path for path in inventory if path.name.casefold() == title_name.casefold())
    def normalized_legacy_title(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        normalized = re.sub(r"[\u2010-\u2015\u2212]", "-", normalized)
        return normalized.casefold()

    legacy_title = normalized_legacy_title(sanitize_filename(title, 150))
    legacy_pattern = re.compile(r"^CVPR\d+_MAIN_[A-Z0-9]+_(.+)\.pdf$", re.IGNORECASE)
    for path in inventory:
        match = legacy_pattern.match(path.name)
        if match and normalized_legacy_title(match.group(1)) == legacy_title:
            candidates.add(path)
    return sorted(candidates, key=lambda path: str(path).casefold())


def _write_report(paths: ProjectPaths, plan: ReclassificationPlan) -> None:
    warning = bool(plan.errors) or not plan.ready
    lines = [
        "# WARNING" if warning else f"# {paths.normalized_venue} {paths.year} PDF Migration",
        "",
        f"Expected: {plan.expected_count}",
        f"Resolved Sources: {len(plan.rows)}",
        f"Moved: {plan.moved}",
        f"Renamed: {plan.renamed}",
        f"Missing: {len(plan.missing)}",
        f"Ambiguous: {len(plan.ambiguous)}",
        f"Target Collision: {len(plan.collisions)}",
        f"Inventory PDFs: {plan.inventory_count}",
        f"Preserved Legacy Non-PDF Files: {plan.preserved_legacy_files}",
        "",
        f"KD_Method_Centric: {plan.class_counts['KD_Method_Centric']}",
        f"Other_KD: {plan.class_counts['Other_KD']}",
    ]
    if plan.errors:
        lines.extend(["", "Issues:"])
        lines.extend(f"- {error}" for error in plan.errors[:MAX_REPORT_DETAILS])
        if len(plan.errors) > MAX_REPORT_DETAILS:
            lines.append(f"- ... and {len(plan.errors) - MAX_REPORT_DETAILS} more")
    status = "OK" if plan.executed and not plan.errors else "READY" if plan.ready else "ERROR"
    lines.extend(["", f"Status: {status}"])
    plan.report_path.parent.mkdir(parents=True, exist_ok=True)
    plan.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan_reclassification(
    library_root: Path,
    venue: str,
    year: int,
    csv_path: Path,
    *,
    expected_counts: dict[str, int] | None = None,
    raise_on_error: bool = True,
) -> ReclassificationPlan:
    """Build a complete move plan without modifying a PDF or directory layout."""
    paths = ProjectPaths(library_root, venue.upper(), year)
    errors: list[str] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    collisions: list[str] = []
    class_counts = {"KD_Method_Centric": 0, "Other_KD": 0}
    rows: list[dict[str, object]] = []

    if not csv_path.exists():
        errors.append(f"Reclassification CSV not found: {csv_path}")
        frame = pd.DataFrame()
    else:
        frame = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        errors.append("Reclassification CSV missing columns: " + ", ".join(missing_columns))

    inventory = sorted(paths.pdf_dir.rglob("*.pdf"), key=lambda path: str(path).casefold()) if paths.pdf_dir.exists() else []
    unexpected_legacy_files: list[Path] = []
    if paths.full_read_pdf_dir.exists():
        unexpected_legacy_files = [
            path for path in paths.full_read_pdf_dir.rglob("*") if path.is_file() and path.suffix.lower() != ".pdf"
        ]
    manifest_by_id, _ = _read_manifest_records(paths)
    used_filenames: set[str] = set()
    destination_keys: dict[str, str] = {}

    if not missing_columns:
        for column in REQUIRED_COLUMNS:
            frame[column] = frame[column].map(clean_cell).str.strip()
        ids = frame[CSV_PAPER_ID]
        duplicate_ids = sorted(set(ids[(ids != "") & ids.duplicated(keep=False)]))
        if duplicate_ids:
            errors.append("Duplicate Paper_ID: " + ", ".join(duplicate_ids))
        if (ids == "").any():
            errors.append(f"Empty Paper_ID: {int((ids == '').sum())}")

        for raw in frame.to_dict(orient="records"):
            paper_id = raw[CSV_PAPER_ID]
            title = raw[CSV_TITLE]
            canonical = CLASS_ALIASES.get(raw[CSV_FINAL_CLASS])
            target_category = raw[CSV_TARGET_CATEGORY]
            assistant = raw[CSV_TARGET_ASSISTANT]
            if canonical is None:
                errors.append(f"Invalid Final_KD_Centrality_Class: {paper_id}")
                continue
            class_counts[canonical] += 1
            if target_category != canonical:
                errors.append(f"Target_Category_Folder mismatch: {paper_id}")
                continue
            if assistant not in ASSISTANT_GROUPS:
                errors.append(f"Invalid Target_Assistant_Folder: {paper_id}")
                continue
            expected_dir = paths.kd_review_assistant_dir(canonical, assistant).resolve()
            target_dir = Path(raw[CSV_TARGET_DIRECTORY]).resolve()
            if target_dir != expected_dir or not _inside(target_dir, paths.pdf_dir):
                errors.append(f"Invalid Target_Directory: {paper_id}")
                continue
            filename, filename_collision = plan_title_pdf_filename(
                title,
                paper_id,
                target_dir,
                used_filenames,
            )
            destination = (target_dir / filename).resolve()
            key = str(destination).casefold()
            if key in destination_keys and destination_keys[key] != paper_id:
                collisions.append(paper_id)
                errors.append(f"Target collision: {paper_id} and {destination_keys[key]}")
                continue
            destination_keys[key] = paper_id

            matches = _resolve_source(
                paper_id,
                title,
                inventory,
                manifest_by_id.get(paper_id, {}),
                paths.pdf_dir,
            )
            if not matches:
                missing.append(paper_id)
                errors.append(f"Missing source PDF: {paper_id}")
                continue
            if len(matches) != 1:
                ambiguous.append(paper_id)
                errors.append(f"Ambiguous source PDF ({len(matches)}): {paper_id}")
                continue
            source = matches[0].resolve()
            if not _valid_pdf(source):
                errors.append(f"Invalid PDF file: {paper_id}")
                continue
            if destination.exists() and destination != source:
                collisions.append(paper_id)
                errors.append(f"Target already exists: {paper_id}")
                continue
            manifest_record = manifest_by_id.get(paper_id, {})
            rows.append(
                {
                    "paper_id": paper_id,
                    "title": title,
                    "secondary_class": canonical,
                    "assistant": assistant,
                    "source": source,
                    "destination": destination,
                    "saved_filename": filename,
                    "filename_collision": filename_collision,
                    "legacy_tier": clean_cell(raw.get(CSV_LEGACY_TIER)).strip(),
                    "pdf_url": _manifest_value(
                        manifest_record,
                        "PDF_URL（官方PDF链接）",
                        "PDF_URL",
                    ),
                }
            )

    resolved_sources = [Path(row["source"]).resolve() for row in rows]
    if len(set(resolved_sources)) != len(resolved_sources):
        errors.append("One source PDF resolves to more than one Paper_ID")
    unresolved_inventory = set(path.resolve() for path in inventory) - set(resolved_sources)
    if not missing_columns and unresolved_inventory:
        errors.append(f"Unresolved/extra PDF files: {len(unresolved_inventory)}")
    if not missing_columns and len(inventory) != len(frame):
        errors.append(f"PDF inventory count mismatch: CSV={len(frame)}, PDFs={len(inventory)}")
    if expected_counts:
        expected_total = sum(expected_counts.values())
        if len(frame) != expected_total:
            errors.append(f"Expected {expected_total} CSV rows, found {len(frame)}")
        for secondary_class, expected in expected_counts.items():
            if class_counts.get(secondary_class, 0) != expected:
                errors.append(
                    f"Expected {secondary_class}={expected}, found {class_counts.get(secondary_class, 0)}"
                )

    ready = not errors and len(rows) == len(frame) == len(inventory)
    plan = ReclassificationPlan(
        rows=rows,
        expected_count=len(frame),
        errors=errors,
        missing=missing,
        ambiguous=ambiguous,
        collisions=collisions,
        class_counts=class_counts,
        inventory_count=len(inventory),
        preserved_legacy_files=len(unexpected_legacy_files),
        report_path=paths.pdf_migration_report,
        ready=ready,
        legacy_full_read_exists=paths.full_read_pdf_dir.exists(),
    )
    _write_report(paths, plan)
    if errors and raise_on_error:
        raise ReclassificationError(f"Reclassification dry-run failed; see {plan.report_path}")
    return plan


def _remove_empty_legacy_tree(root: Path) -> None:
    if not root.exists():
        return
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def execute_reclassification(
    library_root: Path,
    venue: str,
    year: int,
    csv_path: Path,
    *,
    expected_counts: dict[str, int] | None = None,
) -> ReclassificationPlan:
    """Execute a READY plan transactionally, then verify and update the manifest."""
    paths = ProjectPaths(library_root, venue.upper(), year)
    plan = plan_reclassification(
        library_root,
        venue,
        year,
        csv_path,
        expected_counts=expected_counts,
    )
    if not plan.ready:
        raise ReclassificationError("Reclassification plan is not READY")

    moved: list[tuple[Path, Path]] = []
    try:
        for row in plan.rows:
            source = Path(row["source"])
            destination = Path(row["destination"])
            if source == destination:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            moved.append((source, destination))

        destinations = [Path(row["destination"]) for row in plan.rows]
        if len(set(path.resolve() for path in destinations)) != len(destinations):
            raise ReclassificationError("Post-move duplicate destination detected")
        invalid = [path for path in destinations if not _valid_pdf(path)]
        if invalid:
            raise ReclassificationError(f"Post-move missing/invalid PDFs: {len(invalid)}")
        final_pdfs = list(paths.kd_method_centric_pdf_dir.rglob("*.pdf")) + list(
            paths.other_kd_pdf_dir.rglob("*.pdf")
        )
        if len(final_pdfs) != len(plan.rows):
            raise ReclassificationError(
                f"Post-move PDF count mismatch: expected={len(plan.rows)}, found={len(final_pdfs)}"
            )

        manifest_rows = []
        for row in plan.rows:
            destination = Path(row["destination"])
            manifest_rows.append(
                {
                    PAPER_ID_COLUMN: row["paper_id"],
                    "Title（标题）": row["title"],
                    SECONDARY_CLASS_COLUMN: row["secondary_class"],
                    ASSISTANT_GROUP_COLUMN: row["assistant"],
                    ASSISTANT_ORDER_COLUMN: "",
                    BATCH_ID_COLUMN: "",
                    "PDF_URL（官方PDF链接）": row["pdf_url"],
                    "Original_Title（原始标题）": row["title"],
                    "Saved_Filename（保存文件名）": row["saved_filename"],
                    "Filename_Collision（文件名冲突）": (
                        "TRUE" if row["filename_collision"] else "FALSE"
                    ),
                    "Local_PDF_Path（本地PDF路径）": str(destination.resolve()),
                    "Download_Status（下载状态）": "MIGRATED",
                    "File_Size_Bytes（文件大小_字节）": destination.stat().st_size,
                    "Failure_Reason（失败原因）": "",
                    "Final_KD_Centrality_Class（最终KD核心性分类）": row["secondary_class"],
                    "Legacy_Read_Tier（历史阅读层级）": row["legacy_tier"],
                }
            )
        temp_manifest = paths.kd_review_manifest_csv.with_suffix(".csv.tmp")
        write_csv(pd.DataFrame(manifest_rows), temp_manifest, MIGRATION_MANIFEST_COLUMNS)
        temp_manifest.replace(paths.kd_review_manifest_csv)

        if paths.full_read_manifest_csv.exists():
            legacy_backup = paths.full_read_manifest_csv.with_name(
                f"{paths.stem}_FULL_READ_PDF_Manifest_legacy.csv"
            )
            if not legacy_backup.exists():
                paths.full_read_manifest_csv.replace(legacy_backup)

        _remove_empty_legacy_tree(paths.full_read_pdf_dir)
        plan.executed = True
        plan.moved = len(moved)
        plan.renamed = sum(Path(row["source"]).name != Path(row["destination"]).name for row in plan.rows)
        plan.legacy_full_read_exists = paths.full_read_pdf_dir.exists()
        plan.ready = not plan.errors
        _write_report(paths, plan)
        return plan
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(source)
        raise
