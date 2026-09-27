"""Validation and compact QC for the authoritative KD review Assignment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .paths import ProjectPaths
from .utils import clean_cell


PAPER_ID_COLUMN = "Paper_ID（论文编号）"
ASSIGNMENT_TITLE_COLUMN = "Title（论文标题）"
SECONDARY_TITLE_COLUMN = "Title（标题）"
YEAR_COLUMN = "Year（年份）"
VENUE_COLUMN = "Venue（会议/期刊）"
ASSIGNMENT_PDF_URL_COLUMN = "PDF_URL（PDF链接）"
SECONDARY_PDF_URL_COLUMN = "PDF_URL（官方PDF链接）"
SECONDARY_CLASS_COLUMN = "Secondary_Class（二筛分类）"
ASSISTANT_GROUP_COLUMN = "Assistant_Group（助手分组）"
ASSISTANT_ORDER_COLUMN = "Assistant_Order（助手内顺序）"
BATCH_ID_COLUMN = "Batch_ID（批次编号）"

SECONDARY_CLASSES = ("KD_Method_Centric", "Other_KD")
ASSISTANT_GROUPS = tuple(f"Assistant_{index}" for index in range(1, 6))
ASSIGNMENT_COLUMNS = (
    PAPER_ID_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    YEAR_COLUMN,
    VENUE_COLUMN,
    ASSIGNMENT_PDF_URL_COLUMN,
    SECONDARY_CLASS_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
)
SECONDARY_REQUIRED_COLUMNS = (
    PAPER_ID_COLUMN,
    SECONDARY_TITLE_COLUMN,
    SECONDARY_PDF_URL_COLUMN,
)
MAX_QC_DETAILS = 20


class AssignmentValidationError(ValueError):
    """Raised after compact QC is written and before any network request."""


@dataclass
class AssignmentValidationResult:
    assignment: pd.DataFrame
    secondary_review: pd.DataFrame
    errors: list[str]
    warnings: list[str]
    missing_ids: list[str]
    extra_ids: list[str]
    duplicate_count: int
    invalid_count: int
    class_counts: dict[str, int]
    assistant_counts: dict[str, dict[str, int]]
    balanced: dict[str, bool]
    report_path: Path

    @property
    def valid(self) -> bool:
        return not self.errors


def _normalize_title(value: object) -> str:
    return re.sub(r"\s+", " ", clean_cell(value)).strip().casefold()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)


def _duplicate_ids(frame: pd.DataFrame) -> list[str]:
    if PAPER_ID_COLUMN not in frame.columns:
        return []
    ids = frame[PAPER_ID_COLUMN].map(clean_cell).str.strip()
    return sorted(set(ids[(ids != "") & ids.duplicated(keep=False)]))


def _prepare_secondary_pool(
    path: Path,
    secondary_class: str,
    errors: list[str],
) -> pd.DataFrame:
    if not path.exists():
        errors.append(f"Secondary screening file not found: {path}")
        return pd.DataFrame(columns=[*SECONDARY_REQUIRED_COLUMNS, SECONDARY_CLASS_COLUMN])
    frame = _read_csv(path)
    missing = [column for column in SECONDARY_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        errors.append(f"{path.name} missing columns: {', '.join(missing)}")
        return pd.DataFrame(columns=[*SECONDARY_REQUIRED_COLUMNS, SECONDARY_CLASS_COLUMN])
    frame = frame.copy()
    for column in SECONDARY_REQUIRED_COLUMNS:
        frame[column] = frame[column].map(clean_cell).str.strip()
    if SECONDARY_CLASS_COLUMN in frame.columns:
        values = frame[SECONDARY_CLASS_COLUMN].map(clean_cell).str.strip()
        invalid = values[values != secondary_class]
        if not invalid.empty:
            errors.append(f"{path.name} contains {len(invalid)} invalid Secondary_Class values")
    frame[SECONDARY_CLASS_COLUMN] = secondary_class
    return frame


def _write_qc_report(paths: ProjectPaths, result: AssignmentValidationResult) -> None:
    has_warning = bool(result.errors or result.warnings)
    first = result.assistant_counts["KD_Method_Centric"]
    other = result.assistant_counts["Other_KD"]
    lines = [
        "# WARNING" if has_warning else f"# {paths.normalized_venue} {paths.year} KD Review Assignment QC",
        "",
        f"Secondary Review: {len(result.secondary_review)}",
        f"Assignment: {len(result.assignment)}",
        f"KD_Method_Centric: {result.class_counts['KD_Method_Centric']}",
        f"Other_KD: {result.class_counts['Other_KD']}",
        "",
        "KD_Method_Centric Assistant Counts: "
        + " / ".join(str(first[group]) for group in ASSISTANT_GROUPS),
        f"KD_Method_Centric Balanced: {'YES' if result.balanced['KD_Method_Centric'] else 'NO'}",
        "Other_KD Assistant Counts: "
        + " / ".join(str(other[group]) for group in ASSISTANT_GROUPS),
        f"Other_KD Balanced: {'YES' if result.balanced['Other_KD'] else 'NO'}",
        "",
        f"Missing: {len(result.missing_ids)}",
        f"Extra: {len(result.extra_ids)}",
        f"Duplicate: {result.duplicate_count}",
        f"Invalid: {result.invalid_count}",
    ]
    details = [*result.errors, *result.warnings]
    if details:
        lines.extend(["", "Issues:"])
        lines.extend(f"- {detail}" for detail in details[:MAX_QC_DETAILS])
        if len(details) > MAX_QC_DETAILS:
            lines.append(f"- ... and {len(details) - MAX_QC_DETAILS} more")
    lines.extend(["", f"Status: {'ERROR' if result.errors else 'WARNING' if result.warnings else 'OK'}"])
    result.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_kd_review_assignment(
    library_root: Path,
    venue: str,
    year: int,
    raise_on_error: bool = True,
) -> AssignmentValidationResult:
    """Validate both readable secondary classes against one authoritative Assignment."""
    paths = ProjectPaths(library_root, venue.upper(), year)
    paths.ensure_stage("validate-assignment")
    errors: list[str] = []
    warnings: list[str] = []
    invalid_count = 0

    method = _prepare_secondary_pool(paths.kd_method_centric_csv, "KD_Method_Centric", errors)
    other = _prepare_secondary_pool(paths.other_kd_csv, "Other_KD", errors)
    secondary = pd.concat([method, other], ignore_index=True)
    assignment = _read_csv(paths.kd_review_assignment_csv)
    if not paths.kd_review_assignment_csv.exists():
        errors.append(f"Assignment CSV not found: {paths.kd_review_assignment_csv}")
    missing_assignment_columns = [column for column in ASSIGNMENT_COLUMNS if column not in assignment.columns]
    if missing_assignment_columns:
        errors.append("Assignment CSV missing columns: " + ", ".join(missing_assignment_columns))

    secondary_duplicates = _duplicate_ids(secondary)
    assignment_duplicates = _duplicate_ids(assignment)
    duplicate_count = len(secondary_duplicates) + len(assignment_duplicates)
    if secondary_duplicates:
        errors.append("Duplicate Paper_ID in secondary review pools: " + ", ".join(secondary_duplicates))
    if assignment_duplicates:
        errors.append("Duplicate Paper_ID in Assignment: " + ", ".join(assignment_duplicates))

    missing_ids: list[str] = []
    extra_ids: list[str] = []
    class_counts = {secondary_class: 0 for secondary_class in SECONDARY_CLASSES}
    assistant_counts = {
        secondary_class: {group: 0 for group in ASSISTANT_GROUPS}
        for secondary_class in SECONDARY_CLASSES
    }
    balanced = {secondary_class: True for secondary_class in SECONDARY_CLASSES}

    secondary_ready = all(column in secondary.columns for column in SECONDARY_REQUIRED_COLUMNS)
    assignment_ready = not missing_assignment_columns
    if secondary_ready and assignment_ready:
        for column in ASSIGNMENT_COLUMNS:
            assignment[column] = assignment[column].map(clean_cell).str.strip()

        secondary_ids = set(secondary[PAPER_ID_COLUMN]) - {""}
        assignment_ids = set(assignment[PAPER_ID_COLUMN]) - {""}
        missing_ids = sorted(secondary_ids - assignment_ids)
        extra_ids = sorted(assignment_ids - secondary_ids)
        if missing_ids:
            errors.append("Missing Assignment Paper_ID: " + ", ".join(missing_ids))
        if extra_ids:
            errors.append("Extra Assignment Paper_ID: " + ", ".join(extra_ids))
        if len(secondary) != len(assignment):
            errors.append(f"Row count mismatch: Secondary Review={len(secondary)}, Assignment={len(assignment)}")

        checks = (
            ("empty Paper_ID", assignment[assignment[PAPER_ID_COLUMN] == ""]),
            (
                "invalid Secondary_Class",
                assignment[~assignment[SECONDARY_CLASS_COLUMN].isin(SECONDARY_CLASSES)],
            ),
            (
                "invalid Assistant_Group",
                assignment[~assignment[ASSISTANT_GROUP_COLUMN].isin(ASSISTANT_GROUPS)],
            ),
            (
                "invalid Assistant_Order",
                assignment[~assignment[ASSISTANT_ORDER_COLUMN].str.fullmatch(r"[1-9]\d*", na=False)],
            ),
            ("empty Batch_ID", assignment[assignment[BATCH_ID_COLUMN] == ""]),
            ("missing PDF_URL", assignment[assignment[ASSIGNMENT_PDF_URL_COLUMN] == ""]),
            ("invalid Year", assignment[assignment[YEAR_COLUMN] != str(year)]),
            ("invalid Venue", assignment[assignment[VENUE_COLUMN].str.upper() != venue.upper()]),
            ("empty Title", assignment[assignment[ASSIGNMENT_TITLE_COLUMN] == ""]),
        )
        for label, invalid in checks:
            if not invalid.empty:
                invalid_count += len(invalid)
                ids = ", ".join(invalid[PAPER_ID_COLUMN].head(10))
                errors.append(f"{label}: {len(invalid)} ({ids})")

        valid_orders = assignment[assignment[ASSISTANT_ORDER_COLUMN].str.fullmatch(r"[1-9]\d*", na=False)]
        duplicate_orders = valid_orders.duplicated(
            subset=[SECONDARY_CLASS_COLUMN, ASSISTANT_GROUP_COLUMN, ASSISTANT_ORDER_COLUMN],
            keep=False,
        )
        if duplicate_orders.any():
            count = int(duplicate_orders.sum())
            invalid_count += count
            errors.append(f"Duplicate Assistant_Order within a class/group: {count}")

        secondary_by_id = secondary.drop_duplicates(PAPER_ID_COLUMN).set_index(PAPER_ID_COLUMN)
        for row in assignment.to_dict(orient="records"):
            paper_id = row[PAPER_ID_COLUMN]
            if paper_id not in secondary_by_id.index:
                continue
            source = secondary_by_id.loc[paper_id]
            if _normalize_title(row[ASSIGNMENT_TITLE_COLUMN]) != _normalize_title(source[SECONDARY_TITLE_COLUMN]):
                invalid_count += 1
                errors.append(f"Title mismatch: {paper_id}")
            if row[ASSIGNMENT_PDF_URL_COLUMN] != clean_cell(source[SECONDARY_PDF_URL_COLUMN]).strip():
                invalid_count += 1
                errors.append(f"PDF_URL mismatch: {paper_id}")
            if row[SECONDARY_CLASS_COLUMN] != clean_cell(source[SECONDARY_CLASS_COLUMN]).strip():
                invalid_count += 1
                errors.append(f"Secondary_Class mismatch: {paper_id}")

        for secondary_class in SECONDARY_CLASSES:
            class_frame = assignment[assignment[SECONDARY_CLASS_COLUMN] == secondary_class]
            class_counts[secondary_class] = len(class_frame)
            for group in ASSISTANT_GROUPS:
                assistant_counts[secondary_class][group] = int(
                    (class_frame[ASSISTANT_GROUP_COLUMN] == group).sum()
                )
            values = list(assistant_counts[secondary_class].values())
            balanced[secondary_class] = max(values) - min(values) <= 1
            if class_counts[secondary_class] and not balanced[secondary_class]:
                warnings.append(f"{secondary_class} Assistant distribution is not balanced")

    result = AssignmentValidationResult(
        assignment=assignment,
        secondary_review=secondary,
        errors=errors,
        warnings=warnings,
        missing_ids=missing_ids,
        extra_ids=extra_ids,
        duplicate_count=duplicate_count,
        invalid_count=invalid_count,
        class_counts=class_counts,
        assistant_counts=assistant_counts,
        balanced=balanced,
        report_path=paths.assignment_qc_report,
    )
    _write_qc_report(paths, result)
    if errors and raise_on_error:
        raise AssignmentValidationError(
            f"KD review Assignment validation failed; see {paths.assignment_qc_report}"
        )
    return result


def validate_assignment(
    library_root: Path,
    venue: str,
    year: int,
    raise_on_error: bool = True,
):
    """Use the new KD review schema, with read-only legacy FULL_READ fallback."""
    paths = ProjectPaths(library_root, venue.upper(), year)
    new_schema_present = any(
        path.exists()
        for path in (
            paths.kd_method_centric_csv,
            paths.other_kd_csv,
            paths.kd_review_assignment_csv,
        )
    )
    if new_schema_present:
        return validate_kd_review_assignment(
            library_root,
            venue,
            year,
            raise_on_error=raise_on_error,
        )
    from .assignment import validate_full_read_assignment

    return validate_full_read_assignment(
        library_root,
        venue,
        year,
        raise_on_error=raise_on_error,
    )
