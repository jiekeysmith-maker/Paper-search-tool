"""Validation and compact QC for authoritative FULL_READ assignments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .paths import ProjectPaths
from .utils import clean_cell


PAPER_ID_COLUMN = "Paper_ID（论文编号）"
ASSIGNMENT_TITLE_COLUMN = "Title（论文标题）"
FULL_READ_TITLE_COLUMN = "Title（标题）"
YEAR_COLUMN = "Year（年份）"
VENUE_COLUMN = "Venue（会议/期刊）"
ASSIGNMENT_PDF_URL_COLUMN = "PDF_URL（PDF链接）"
FULL_READ_PDF_URL_COLUMN = "PDF_URL（官方PDF链接）"
READ_TIER_COLUMN = "Read_Tier（阅读层级）"
ASSISTANT_GROUP_COLUMN = "Assistant_Group（助手分组）"
ASSISTANT_ORDER_COLUMN = "Assistant_Order（助手内顺序）"
BATCH_ID_COLUMN = "Batch_ID（批次编号）"
SECONDARY_DECISION_COLUMN = "Secondary_Decision（二次筛选结果）"

READ_TIERS = ("Read_First", "Read_Normal")
ASSISTANT_GROUPS = tuple(f"Assistant_{index}" for index in range(1, 6))
ASSIGNMENT_COLUMNS = (
    PAPER_ID_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    YEAR_COLUMN,
    VENUE_COLUMN,
    ASSIGNMENT_PDF_URL_COLUMN,
    READ_TIER_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
)
FULL_READ_REQUIRED_COLUMNS = (
    PAPER_ID_COLUMN,
    FULL_READ_TITLE_COLUMN,
    FULL_READ_PDF_URL_COLUMN,
    SECONDARY_DECISION_COLUMN,
)
MAX_QC_DETAILS = 20


class AssignmentValidationError(ValueError):
    """Raised after compact QC is written and before any network request."""


@dataclass
class AssignmentValidationResult:
    assignment: pd.DataFrame
    full_read: pd.DataFrame
    errors: list[str]
    warnings: list[str]
    missing_ids: list[str]
    extra_ids: list[str]
    duplicate_count: int
    invalid_count: int
    tier_counts: dict[str, int]
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
    if frame.empty or PAPER_ID_COLUMN not in frame.columns:
        return []
    ids = frame[PAPER_ID_COLUMN].map(clean_cell).str.strip()
    return sorted(set(ids[(ids != "") & ids.duplicated(keep=False)]))


def _write_qc_report(paths: ProjectPaths, result: AssignmentValidationResult) -> None:
    has_warning = bool(result.errors or result.warnings)
    header = "# WARNING" if has_warning else f"# {paths.normalized_venue} {paths.year} Assignment QC"
    first_counts = result.assistant_counts["Read_First"]
    normal_counts = result.assistant_counts["Read_Normal"]
    lines = [
        header,
        "",
        f"FULL_READ: {len(result.full_read)}",
        f"Assignment: {len(result.assignment)}",
        f"Read_First: {result.tier_counts['Read_First']}",
        f"Read_Normal: {result.tier_counts['Read_Normal']}",
        "",
        "Read_First Assistant Counts: "
        + " / ".join(str(first_counts[group]) for group in ASSISTANT_GROUPS),
        f"Read_First Balanced: {'YES' if result.balanced['Read_First'] else 'NO'}",
        "Read_Normal Assistant Counts: "
        + " / ".join(str(normal_counts[group]) for group in ASSISTANT_GROUPS),
        f"Read_Normal Balanced: {'YES' if result.balanced['Read_Normal'] else 'NO'}",
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


def validate_full_read_assignment(
    library_root: Path,
    venue: str,
    year: int,
    raise_on_error: bool = True,
) -> AssignmentValidationResult:
    """Validate FULL_READ and its authoritative assignment, then write compact QC."""
    paths = ProjectPaths(library_root, venue.upper(), year)
    paths.ensure_stage("validate-assignment")
    full_read = _read_csv(paths.full_read_csv)
    assignment = _read_csv(paths.full_read_assignment_csv)
    errors: list[str] = []
    warnings: list[str] = []
    invalid_count = 0

    missing_full_columns = [column for column in FULL_READ_REQUIRED_COLUMNS if column not in full_read.columns]
    missing_assignment_columns = [column for column in ASSIGNMENT_COLUMNS if column not in assignment.columns]
    if full_read.empty:
        errors.append(f"FULL_READ.csv missing or empty: {paths.full_read_csv}")
    if assignment.empty:
        errors.append(f"Assignment CSV missing or empty: {paths.full_read_assignment_csv}")
    if missing_full_columns:
        errors.append("FULL_READ.csv missing columns: " + ", ".join(missing_full_columns))
    if missing_assignment_columns:
        errors.append("Assignment CSV missing columns: " + ", ".join(missing_assignment_columns))

    full_duplicates = _duplicate_ids(full_read)
    assignment_duplicates = _duplicate_ids(assignment)
    duplicate_count = len(full_duplicates) + len(assignment_duplicates)
    if full_duplicates:
        errors.append("Duplicate Paper_ID in FULL_READ: " + ", ".join(full_duplicates))
    if assignment_duplicates:
        errors.append("Duplicate Paper_ID in Assignment: " + ", ".join(assignment_duplicates))

    missing_ids: list[str] = []
    extra_ids: list[str] = []
    tier_counts = {tier: 0 for tier in READ_TIERS}
    assistant_counts = {
        tier: {group: 0 for group in ASSISTANT_GROUPS} for tier in READ_TIERS
    }
    balanced = {tier: True for tier in READ_TIERS}

    if not missing_full_columns and not missing_assignment_columns and not full_read.empty and not assignment.empty:
        for column in FULL_READ_REQUIRED_COLUMNS:
            full_read[column] = full_read[column].map(clean_cell).str.strip()
        for column in ASSIGNMENT_COLUMNS:
            assignment[column] = assignment[column].map(clean_cell).str.strip()

        invalid_decisions = full_read[full_read[SECONDARY_DECISION_COLUMN] != "FULL_READ"]
        if not invalid_decisions.empty:
            invalid_count += len(invalid_decisions)
            errors.append(f"FULL_READ.csv contains {len(invalid_decisions)} non-FULL_READ decisions")

        full_ids = set(full_read[PAPER_ID_COLUMN]) - {""}
        assignment_ids = set(assignment[PAPER_ID_COLUMN]) - {""}
        missing_ids = sorted(full_ids - assignment_ids)
        extra_ids = sorted(assignment_ids - full_ids)
        if missing_ids:
            errors.append("Missing Assignment Paper_ID: " + ", ".join(missing_ids))
        if extra_ids:
            errors.append("Extra Assignment Paper_ID: " + ", ".join(extra_ids))
        if len(full_read) != len(assignment):
            errors.append(f"Row count mismatch: FULL_READ={len(full_read)}, Assignment={len(assignment)}")

        empty_ids = assignment[assignment[PAPER_ID_COLUMN] == ""]
        invalid_tiers = assignment[~assignment[READ_TIER_COLUMN].isin(READ_TIERS)]
        invalid_assistants = assignment[~assignment[ASSISTANT_GROUP_COLUMN].isin(ASSISTANT_GROUPS)]
        invalid_orders = assignment[
            ~assignment[ASSISTANT_ORDER_COLUMN].str.fullmatch(r"[1-9]\d*", na=False)
        ]
        empty_batches = assignment[assignment[BATCH_ID_COLUMN] == ""]
        empty_urls = assignment[assignment[ASSIGNMENT_PDF_URL_COLUMN] == ""]
        invalid_years = assignment[assignment[YEAR_COLUMN] != str(year)]
        invalid_venues = assignment[assignment[VENUE_COLUMN].str.upper() != venue.upper()]
        invalid_titles = assignment[assignment[ASSIGNMENT_TITLE_COLUMN] == ""]
        invalid_frames = (
            ("empty Paper_ID", empty_ids),
            ("invalid Read_Tier", invalid_tiers),
            ("invalid Assistant_Group", invalid_assistants),
            ("invalid Assistant_Order", invalid_orders),
            ("empty Batch_ID", empty_batches),
            ("missing PDF_URL", empty_urls),
            ("invalid Year", invalid_years),
            ("invalid Venue", invalid_venues),
            ("empty Title", invalid_titles),
        )
        for label, invalid in invalid_frames:
            if not invalid.empty:
                invalid_count += len(invalid)
                ids = ", ".join(invalid[PAPER_ID_COLUMN].head(10))
                errors.append(f"{label}: {len(invalid)} ({ids})")

        valid_order_rows = assignment[assignment[ASSISTANT_ORDER_COLUMN].str.fullmatch(r"[1-9]\d*", na=False)]
        duplicate_orders = valid_order_rows.duplicated(
            subset=[READ_TIER_COLUMN, ASSISTANT_GROUP_COLUMN, ASSISTANT_ORDER_COLUMN],
            keep=False,
        )
        if duplicate_orders.any():
            count = int(duplicate_orders.sum())
            invalid_count += count
            errors.append(f"Duplicate Assistant_Order within a tier/group: {count}")

        full_by_id = full_read.drop_duplicates(PAPER_ID_COLUMN).set_index(PAPER_ID_COLUMN)
        for row in assignment.to_dict(orient="records"):
            paper_id = row[PAPER_ID_COLUMN]
            if paper_id not in full_by_id.index:
                continue
            full_row = full_by_id.loc[paper_id]
            if _normalize_title(row[ASSIGNMENT_TITLE_COLUMN]) != _normalize_title(full_row[FULL_READ_TITLE_COLUMN]):
                invalid_count += 1
                errors.append(f"Title mismatch: {paper_id}")
            full_url = clean_cell(full_row[FULL_READ_PDF_URL_COLUMN]).strip()
            if full_url and row[ASSIGNMENT_PDF_URL_COLUMN] != full_url:
                invalid_count += 1
                errors.append(f"PDF_URL mismatch: {paper_id}")

        for tier in READ_TIERS:
            tier_frame = assignment[assignment[READ_TIER_COLUMN] == tier]
            tier_counts[tier] = len(tier_frame)
            for group in ASSISTANT_GROUPS:
                assistant_counts[tier][group] = int((tier_frame[ASSISTANT_GROUP_COLUMN] == group).sum())
            values = list(assistant_counts[tier].values())
            balanced[tier] = max(values) - min(values) <= 1
            if tier_counts[tier] and not balanced[tier]:
                warnings.append(f"{tier} Assistant distribution is not balanced")

    result = AssignmentValidationResult(
        assignment=assignment,
        full_read=full_read,
        errors=errors,
        warnings=warnings,
        missing_ids=missing_ids,
        extra_ids=extra_ids,
        duplicate_count=duplicate_count,
        invalid_count=invalid_count,
        tier_counts=tier_counts,
        assistant_counts=assistant_counts,
        balanced=balanced,
        report_path=paths.assignment_qc_report,
    )
    _write_qc_report(paths, result)
    if errors and raise_on_error:
        raise AssignmentValidationError(
            f"FULL_READ Assignment validation failed; see {paths.assignment_qc_report}"
        )
    return result
