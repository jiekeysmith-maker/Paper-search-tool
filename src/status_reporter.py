"""Compact human-readable Venue-Year status and quality-control report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .downloader import _valid_pdf
from .paths import ProjectPaths
from .utils import clean_cell, setup_logger


ID_COLUMN = "Paper_ID（论文编号）"
TITLE_COLUMN = "Title（标题）"
CRAWL_STATUS_COLUMN = "Crawl_Status（抓取状态）"
DOWNLOAD_STATUS_COLUMN = "Download_Status（下载状态）"
LOCAL_PATH_COLUMN = "Local_PDF_Path（本地PDF路径）"
RULE_VERSION_COLUMN = "Rule_Version（规则版本）"
SUCCESS_DOWNLOAD_STATUSES = {"DOWNLOADED", "SKIPPED_EXISTS", "ALREADY_EXISTS", "MIGRATED"}
FAILED_DOWNLOAD_STATUSES = {"FAILED", "MISSING_URL", "INVALID_EXISTING_FILE"}
MAX_ISSUE_DETAILS = 20


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)


def _duplicate_count(frame: pd.DataFrame) -> int:
    if frame.empty or ID_COLUMN not in frame.columns:
        return 0
    ids = frame[ID_COLUMN].map(clean_cell)
    return int(ids[ids != ""].duplicated(keep=False).sum())


def _duplicate_details(frame: pd.DataFrame) -> list[str]:
    if frame.empty or ID_COLUMN not in frame.columns:
        return []
    ids = frame[ID_COLUMN].map(clean_cell)
    duplicate_ids = sorted(set(ids[(ids != "") & ids.duplicated(keep=False)]))
    details = []
    for paper_id in duplicate_ids:
        matching = frame[ids == paper_id]
        title = clean_cell(matching.iloc[0].get(TITLE_COLUMN)) if not matching.empty else ""
        details.append(f"- {paper_id} — {title} — duplicate Paper_ID")
    return details


def _pool_qc(
    input_frame: pd.DataFrame,
    manifest: pd.DataFrame,
) -> tuple[int, int, int, int, int, list[str]]:
    expected = len(input_frame)
    if input_frame.empty:
        return 0, 0, 0, 0, _duplicate_count(manifest), []

    expected_by_id = {
        clean_cell(row.get(ID_COLUMN)): clean_cell(row.get(TITLE_COLUMN))
        for row in input_frame.to_dict(orient="records")
        if clean_cell(row.get(ID_COLUMN))
    }
    successful_ids: set[str] = set()
    failed_ids: set[str] = set()
    invalid_file_ids: set[str] = set()
    details: list[str] = []

    for row in manifest.to_dict(orient="records"):
        paper_id = clean_cell(row.get(ID_COLUMN))
        title = clean_cell(row.get(TITLE_COLUMN)) or expected_by_id.get(paper_id, "")
        status = clean_cell(row.get(DOWNLOAD_STATUS_COLUMN))
        if status in SUCCESS_DOWNLOAD_STATUSES:
            local_path = clean_cell(row.get(LOCAL_PATH_COLUMN))
            if local_path and _valid_pdf(Path(local_path)):
                successful_ids.add(paper_id)
            else:
                invalid_file_ids.add(paper_id)
                details.append(f"- {paper_id} — {title} — missing/invalid local PDF")
        elif status in FAILED_DOWNLOAD_STATUSES:
            failed_ids.add(paper_id)
            details.append(f"- {paper_id} — {title} — {status}")

    missing_ids = set(expected_by_id) - successful_ids - failed_ids
    missing_ids.update(invalid_file_ids)
    for paper_id in sorted(missing_ids):
        if paper_id not in invalid_file_ids:
            details.append(f"- {paper_id} — {expected_by_id.get(paper_id, '')} — missing")

    duplicates = _duplicate_count(input_frame) + _duplicate_count(manifest)
    return expected, len(successful_ids), len(failed_ids), len(missing_ids), duplicates, details


def generate_report(root: Path, venue: str, year: int) -> Path:
    """Write one small report; detailed diagnostics remain in logs and CSV files."""
    paths = ProjectPaths(root, venue.upper(), year)
    paths.ensure_stage("report")
    logger = setup_logger(f"report.{venue}.{year}", paths.report_log)

    raw = _read_csv(paths.raw_csv)
    screening = _read_csv(paths.screening_csv)
    method_centric = _read_csv(paths.kd_method_centric_csv)
    other_kd = _read_csv(paths.other_kd_csv)
    kd_review = pd.concat([method_centric, other_kd], ignore_index=True)
    kd_manifest = _read_csv(paths.kd_review_manifest_csv)
    # Read-only fallback keeps historical Venue-Year status usable.
    if kd_review.empty and paths.full_read_csv.exists():
        kd_review = _read_csv(paths.full_read_csv)
    if kd_manifest.empty and paths.full_read_manifest_csv.exists():
        kd_manifest = _read_csv(paths.full_read_manifest_csv)

    crawl_success = (
        int((raw[CRAWL_STATUS_COLUMN].map(clean_cell) == "SUCCESS").sum())
        if not raw.empty and CRAWL_STATUS_COLUMN in raw.columns
        else 0
    )
    crawl_failures = len(raw) - crawl_success if not raw.empty else 0
    review_expected, review_downloaded, review_failed, review_missing, review_duplicates, review_details = _pool_qc(
        kd_review, kd_manifest
    )
    duplicates = _duplicate_count(raw) + _duplicate_count(screening) + review_duplicates
    missing = review_missing
    failed = review_failed

    secondary_complete = any(
        path.exists()
        for path in (
            paths.secondary_screening_csv,
            paths.kd_method_centric_csv,
            paths.other_kd_csv,
            paths.exclude_csv,
            paths.full_read_csv,
        )
    )
    rule_versions = (
        sorted(
            {
                clean_cell(value)
                for value in screening[RULE_VERSION_COLUMN]
                if clean_cell(value)
            }
        )
        if not screening.empty and RULE_VERSION_COLUMN in screening.columns
        else []
    )
    rules_version = ", ".join(rule_versions) if rule_versions else "NOT FOUND"
    issues: list[str] = []
    if raw.empty:
        issues.append("- Raw paper pool: NOT FOUND")
    if screening.empty:
        issues.append("- Screening results: NOT FOUND")
    if not secondary_complete:
        issues.append("- Secondary screening results: NOT FOUND")
    if crawl_failures:
        issues.append(f"- Crawl failures: {crawl_failures}")
        failed_rows = raw[raw[CRAWL_STATUS_COLUMN].map(clean_cell) != "SUCCESS"]
        for row in failed_rows.head(MAX_ISSUE_DETAILS).to_dict(orient="records"):
            issues.append(
                f"- {clean_cell(row.get(ID_COLUMN))} — {clean_cell(row.get(TITLE_COLUMN))} — "
                f"crawl {clean_cell(row.get(CRAWL_STATUS_COLUMN)) or 'FAILED'}"
            )
    issues.extend(review_details)
    if duplicates:
        issues.append(f"- Duplicate Paper_ID rows: {duplicates}")
        duplicate_details = (
            _duplicate_details(raw)
            + _duplicate_details(screening)
            + _duplicate_details(kd_review)
            + _duplicate_details(kd_manifest)
        )
        issues.extend(list(dict.fromkeys(duplicate_details)))

    lines = [
        f"# {paths.normalized_venue} {paths.year} Status" if not issues else "# WARNING",
        "",
        f"- Crawl: {crawl_success}/{len(raw)} SUCCESS" if not raw.empty else "- Crawl: NOT FOUND",
        f"- Screening: COMPLETE ({len(screening)})" if not screening.empty else "- Screening: NOT FOUND",
        f"- Rules Version: {rules_version}",
        f"- Secondary Screening: {'COMPLETE' if secondary_complete else 'NOT FOUND'}",
        f"- KD REVIEW PDF: {review_downloaded}/{review_expected}" if review_expected else "- KD REVIEW PDF: NOT REQUESTED",
        f"- Failed: {failed}",
        f"- Missing: {missing}",
        f"- Duplicate: {duplicates}",
        "",
    ]
    if issues:
        lines.extend(["## Problems", "", *issues[:MAX_ISSUE_DETAILS]])
        if len(issues) > MAX_ISSUE_DETAILS:
            lines.append(f"- ... and {len(issues) - MAX_ISSUE_DETAILS} more; see CSV/Manifest and logs.")
        lines.extend(
            [
                "",
                "## Action required",
                "",
                "Review the affected Paper_ID entries. For PDF failures, run the matching --retry-failed command.",
                "",
                "Status: WARNING",
            ]
        )
    else:
        lines.append("Status: OK")

    paths.status_report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("Compact status report written: %s", paths.status_report)
    return paths.status_report
