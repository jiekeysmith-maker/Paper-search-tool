"""Official PDF downloader with Windows-safe filenames and a persistent manifest."""

from __future__ import annotations

import re
import time
from http.client import IncompleteRead
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .kd_assignment import (
    ASSIGNMENT_PDF_URL_COLUMN,
    ASSIGNMENT_TITLE_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
    PAPER_ID_COLUMN,
    SECONDARY_CLASS_COLUMN,
    validate_kd_review_assignment,
)
from .paths import ProjectPaths
from .pdf_naming import plan_title_pdf_filename
from .utils import (
    DECISION_AMBIGUOUS,
    DECISION_KEEP,
    DECISION_MAYBE,
    MANIFEST_COLUMNS,
    SCREENING_COLUMNS,
    clean_cell,
    load_yaml,
    setup_logger,
    write_csv,
    write_xlsx,
)


SECONDARY_DECISION_COLUMN = "Secondary_Decision（二次筛选结果）"
READ_ORDER_COLUMN = "Read_Order（全文阅读顺序）"
READ_FIRST = "Read_First（优先全文读）"
READ_NORMAL = "Read_Normal（正常全文读）"
READ_ORDER_FOLDERS = {
    READ_FIRST: "Read_First",
    READ_NORMAL: "Read_Normal",
}
BASIC_SECONDARY_REQUIRED_COLUMNS = [
    "Paper_ID（论文编号）",
    "Title（标题）",
    "PDF_URL（官方PDF链接）",
    SECONDARY_DECISION_COLUMN,
]
SECONDARY_REQUIRED_COLUMNS = [
    *BASIC_SECONDARY_REQUIRED_COLUMNS,
    READ_ORDER_COLUMN,
]
SECONDARY_MANIFEST_COLUMNS = [
    "Paper_ID（论文编号）",
    "Title（标题）",
    SECONDARY_DECISION_COLUMN,
    READ_ORDER_COLUMN,
    "PDF_URL（官方PDF链接）",
    "Download_Status（下载状态）",
    "Local_PDF_Path（本地PDF路径）",
    "File_Size_Bytes（文件大小_字节）",
    "Error（错误信息）",
]
KD_REVIEW_MANIFEST_COLUMNS = [
    PAPER_ID_COLUMN,
    "Title（标题）",
    SECONDARY_CLASS_COLUMN,
    ASSISTANT_GROUP_COLUMN,
    ASSISTANT_ORDER_COLUMN,
    BATCH_ID_COLUMN,
    "PDF_URL（官方PDF链接）",
    "Original_Title（原始标题）",
    "Saved_Filename（保存文件名）",
    "Filename_Collision（文件名冲突）",
    "Local_PDF_Path（本地PDF路径）",
    "Download_Status（下载状态）",
    "File_Size_Bytes（文件大小_字节）",
    "Failure_Reason（失败原因）",
]
# Deprecated import alias retained for historical tests and integrations.
FULL_READ_MANIFEST_COLUMNS = KD_REVIEW_MANIFEST_COLUMNS


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(value: str, max_length: int = 150) -> str:
    """Return a readable Windows-safe filename component."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = "untitled"
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:max_length].rstrip(" .")


def build_pdf_filename(paper_id: str, title: str, max_length: int = 180) -> str:
    """Build `Paper_ID__Short_Title.pdf` while keeping path length manageable."""
    safe_id = sanitize_filename(paper_id, 60)
    budget = max(20, max_length - len(safe_id) - len("__.pdf"))
    safe_title = sanitize_filename(title, budget)
    return f"{safe_id}__{safe_title}.pdf"


def build_secondary_pdf_filename(paper_id: str, title: str, max_length: int = 180) -> str:
    """Build a secondary-screening `<Paper_ID>_<Sanitized_Title>.pdf` filename."""
    safe_id = sanitize_filename(paper_id, 60)
    budget = max(20, max_length - len(safe_id) - len("_.pdf"))
    safe_title = sanitize_filename(title, budget)
    return f"{safe_id}_{safe_title}.pdf"


def validate_secondary_pool_frame(
    frame: pd.DataFrame,
    source_path: Path,
    decision: str,
    require_read_order: bool = False,
) -> pd.DataFrame:
    """Validate one topic-controller pool before any directory or network action."""
    required = list(BASIC_SECONDARY_REQUIRED_COLUMNS)
    if require_read_order:
        required.append(READ_ORDER_COLUMN)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{source_path.name} missing required columns: {', '.join(missing)}; file={source_path}")

    validated = frame.copy()
    for column in required:
        validated[column] = validated[column].map(clean_cell).str.strip()

    invalid_decisions = sorted(
        value for value in validated[SECONDARY_DECISION_COLUMN].unique() if value != decision
    )
    if invalid_decisions:
        raise ValueError(
            f"{source_path.name} contains non-{decision} Secondary_Decision values: "
            + ", ".join(repr(value) for value in invalid_decisions)
        )

    if require_read_order:
        invalid_read_orders = sorted(
            value for value in validated[READ_ORDER_COLUMN].unique() if value not in READ_ORDER_FOLDERS
        )
        if invalid_read_orders:
            raise ValueError(
                f"{source_path.name} contains invalid Read_Order values: "
                + ", ".join(repr(value) for value in invalid_read_orders)
                + f"; allowed={list(READ_ORDER_FOLDERS)}"
            )

    empty_ids = validated.index[validated["Paper_ID（论文编号）"] == ""].tolist()
    if empty_ids:
        raise ValueError(
            f"{source_path.name} contains empty Paper_ID at data rows: {[index + 2 for index in empty_ids]}"
        )

    duplicates = sorted(
        validated.loc[
            validated["Paper_ID（论文编号）"].duplicated(keep=False), "Paper_ID（论文编号）"
        ].unique()
    )
    if duplicates:
        raise ValueError(f"{source_path.name} contains duplicate Paper_ID values: {', '.join(duplicates)}")

    empty_urls = validated.index[validated["PDF_URL（官方PDF链接）"] == ""].tolist()
    if empty_urls:
        raise ValueError(
            f"{source_path.name} contains empty PDF_URL at data rows: {[index + 2 for index in empty_urls]}"
        )

    return validated


def load_full_read_csv(source_path: Path, require_read_order: bool = False) -> pd.DataFrame:
    """Read the fixed FULL_READ CSV and fail before download on structural errors."""
    if not source_path.exists():
        raise FileNotFoundError(f"FULL_READ.csv not found: {source_path}")
    frame = pd.read_csv(source_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    return validate_secondary_pool_frame(
        frame,
        source_path,
        "FULL_READ",
        require_read_order=require_read_order,
    )


def load_reserve_csv(source_path: Path) -> pd.DataFrame:
    """Read and validate RESERVE.csv without consulting FULL_READ.csv."""
    if not source_path.exists():
        raise FileNotFoundError(f"RESERVE.csv not found: {source_path}")
    frame = pd.read_csv(source_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    return validate_secondary_pool_frame(frame, source_path, "RESERVE")


def _session(http_config: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": http_config.get("user_agent", "PaperSearchTool/1.0"),
            "Accept": "application/pdf,*/*;q=0.8",
        }
    )
    retries = int(http_config.get("retries", 3))
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=float(http_config.get("backoff_factor", 1.0)),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _secondary_session(http_config: dict) -> requests.Session:
    """Create a no-auto-retry session; secondary retries are bounded explicitly."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": http_config.get("user_agent", "PaperSearchTool/1.0"),
            "Accept": "application/pdf,*/*;q=0.8",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=0, connect=0, read=0)))
    return session


def download_pdf_file(
    session: requests.Session,
    url: str,
    destination: Path,
    timeout: float,
    force: bool = False,
) -> tuple[str, int]:
    """Download one PDF atomically and validate its signature."""
    if destination.exists() and destination.stat().st_size > 4 and not force:
        with destination.open("rb") as handle:
            if handle.read(5) == b"%PDF-":
                return "ALREADY_EXISTS", destination.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        handle.write(chunk)
        with temp_path.open("rb") as handle:
            signature = handle.read(5)
        if signature != b"%PDF-":
            raise ValueError("Response is not a PDF (missing %PDF- signature)")
        temp_path.replace(destination)
        return "DOWNLOADED", destination.stat().st_size
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


RETRYABLE_DOWNLOAD_ERRORS = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
    IncompleteRead,
)


def download_pdf_with_retries(
    session: requests.Session,
    url: str,
    destination: Path,
    timeout: float,
    force: bool = False,
    max_retries: int = 3,
    retry_delays: tuple[float, ...] = (2.0, 5.0, 10.0),
    sleep_func: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """Download atomically with at most three bounded retries for transient network failures."""
    for attempt in range(max_retries + 1):
        try:
            return download_pdf_file(session, url, destination, timeout, force=force)
        except RETRYABLE_DOWNLOAD_ERRORS:
            if attempt >= max_retries:
                raise
            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
            sleep_func(delay)
    raise RuntimeError("unreachable retry state")


def _folder_for_decision(decision: str) -> str:
    if decision == DECISION_KEEP:
        return "RULE_KEEP"
    if decision == DECISION_MAYBE:
        return "RULE_MAYBE"
    if decision == DECISION_AMBIGUOUS:
        return "RULE_AMBIGUOUS"
    return "OTHER"


def _rewrite_screening_outputs(frame: pd.DataFrame, paths: ProjectPaths) -> None:
    write_csv(frame, paths.screening_csv, SCREENING_COLUMNS)
    write_xlsx(frame, paths.screening_xlsx, SCREENING_COLUMNS)
    for decision, output_path in (
        (DECISION_KEEP, paths.rule_keep_csv),
        (DECISION_MAYBE, paths.rule_maybe_csv),
        (DECISION_AMBIGUOUS, paths.rule_ambiguous_csv),
    ):
        write_csv(frame[frame["Decision（筛选决定）"] == decision], output_path, SCREENING_COLUMNS)


def download_candidates(
    root: Path,
    venue: str,
    year: int,
    rules_path: Path,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Legacy explicit-only RULE_KEEP/RULE_MAYBE PDF workflow."""
    paths = ProjectPaths(root, venue.upper(), year)
    if not paths.screening_csv.exists():
        raise FileNotFoundError(f"Screening results not found: {paths.screening_csv}")
    paths.ensure_stage("download-legacy")
    logger = setup_logger(f"download.{venue}.{year}", paths.legacy_download_log)
    rules = load_yaml(rules_path)
    source = load_yaml(source_config_path)
    download_config = rules.get("downloads", {})
    decisions = set(download_config.get("decisions", [DECISION_KEEP, DECISION_MAYBE]))
    if download_config.get("download_ambiguous", False):
        decisions.add(DECISION_AMBIGUOUS)

    frame = pd.read_csv(paths.screening_csv, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    candidates = frame[frame["Decision（筛选决定）"].isin(decisions)]
    if limit is not None:
        candidates = candidates.head(limit)
    session = _session(source.get("http", {}))
    timeout = float(source.get("http", {}).get("timeout_seconds", 30))
    request_interval = float(source.get("http", {}).get("request_interval_seconds", 0.6))

    manifest_path = paths.legacy_manifest_csv
    if manifest_path.exists():
        manifest = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        manifest_by_id = {clean_cell(row["Paper_ID（论文编号）"]): row for row in manifest.to_dict(orient="records")}
    else:
        manifest_by_id = {}

    for idx, row in candidates.iterrows():
        paper_id = clean_cell(row["Paper_ID（论文编号）"])
        title = clean_cell(row["Title（标题）"])
        decision = clean_cell(row["Decision（筛选决定）"])
        pdf_url = clean_cell(row["PDF_URL（官方PDF链接）"])
        folder = paths.legacy_pdf_dir(_folder_for_decision(decision))
        destination = folder / build_pdf_filename(paper_id, title)
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        status = "FAILED"
        error = ""
        size = 0
        if not pdf_url:
            status = "MISSING_URL"
            error = "Official PDF URL is empty"
        else:
            try:
                status, size = download_pdf_file(session, pdf_url, destination, timeout, force=force)
                logger.info("PDF %s: %s", status, destination)
                if status == "DOWNLOADED":
                    time.sleep(request_interval)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("PDF failed: %s", pdf_url)
        local_path = str(destination.resolve()) if status in {"DOWNLOADED", "ALREADY_EXISTS"} else ""
        frame.at[idx, "Download_Status（下载状态）"] = status
        frame.at[idx, "Local_PDF_Path（本地PDF路径）"] = local_path
        manifest_by_id[paper_id] = {
            "Paper_ID（论文编号）": paper_id,
            "Title（标题）": title,
            "Decision（筛选决定）": decision,
            "PDF_URL（官方PDF链接）": pdf_url,
            "Local_PDF_Path（本地PDF路径）": local_path,
            "Download_Status（下载状态）": status,
            "Download_Error（下载错误）": error,
            "File_Size（文件大小）": size,
            "Download_Time（下载时间）": timestamp,
        }

    manifest_frame = pd.DataFrame(list(manifest_by_id.values())).reindex(columns=MANIFEST_COLUMNS, fill_value="")
    write_csv(manifest_frame, manifest_path, MANIFEST_COLUMNS)
    _rewrite_screening_outputs(frame, paths)
    logger.info("Download stage complete: selected=%d", len(candidates))
    return manifest_frame


def _valid_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 4:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _read_secondary_manifest(
    manifest_path: Path,
    pool_name: str,
    required: bool = False,
) -> pd.DataFrame:
    if not manifest_path.exists():
        if required:
            raise FileNotFoundError(f"{pool_name} PDF manifest not found: {manifest_path}")
        return pd.DataFrame(columns=SECONDARY_MANIFEST_COLUMNS)
    frame = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    required_columns = {
        "Paper_ID（论文编号）",
        "Download_Status（下载状态）",
        "Local_PDF_Path（本地PDF路径）",
    }
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise ValueError(f"Existing {pool_name} manifest is missing columns: {', '.join(missing)}")
    if frame["Paper_ID（论文编号）"].duplicated().any():
        raise ValueError(f"Existing {pool_name} manifest contains duplicate Paper_ID values")
    return frame


def _pool_input_path(paths: ProjectPaths, pool_name: str) -> Path:
    return paths.full_read_csv if pool_name == "FULL_READ" else paths.reserve_csv


def _pool_manifest_path(paths: ProjectPaths, pool_name: str) -> Path:
    return paths.full_read_manifest_csv if pool_name == "FULL_READ" else paths.reserve_manifest_csv


def _secondary_destination(
    paths: ProjectPaths,
    pool_name: str,
    paper_id: str,
    title: str,
    read_order: str = "",
) -> Path:
    filename = build_secondary_pdf_filename(paper_id, title)
    if pool_name == "FULL_READ":
        return paths.full_read_order_dir(READ_ORDER_FOLDERS[read_order]) / filename
    return paths.reserve_pdf_dir / filename


def _download_secondary_pool(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    pool_name: str,
    limit: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
) -> pd.DataFrame:
    """Download one validated secondary-screening pool with shared safety logic."""
    paths = ProjectPaths(root, venue.upper(), year)
    source_path = _pool_input_path(paths, pool_name)
    pool = load_full_read_csv(source_path) if pool_name == "FULL_READ" else load_reserve_csv(source_path)
    input_by_id = {
        clean_cell(row["Paper_ID（论文编号）"]): row for row in pool.to_dict(orient="records")
    }
    manifest_path = _pool_manifest_path(paths, pool_name)
    previous = _read_secondary_manifest(manifest_path, pool_name, required=retry_failed)
    manifest_by_id = {
        clean_cell(row["Paper_ID（论文编号）"]): row for row in previous.to_dict(orient="records")
    }

    if retry_failed:
        failed_ids = [
            clean_cell(row["Paper_ID（论文编号）"])
            for row in previous.to_dict(orient="records")
            if clean_cell(row["Download_Status（下载状态）"]) == "FAILED"
        ]
        missing_ids = [paper_id for paper_id in failed_ids if paper_id not in input_by_id]
        if missing_ids:
            raise ValueError(
                f"FAILED manifest Paper_ID values missing from {pool_name}.csv: {', '.join(missing_ids)}"
            )
        candidates = pd.DataFrame([input_by_id[paper_id] for paper_id in failed_ids])
    else:
        candidates = pool.copy()
    if limit is not None:
        candidates = candidates.head(limit)

    if pool_name == "FULL_READ":
        for paper_id, manifest_row in manifest_by_id.items():
            if paper_id in input_by_id:
                manifest_row[READ_ORDER_COLUMN] = clean_cell(input_by_id[paper_id][READ_ORDER_COLUMN])

    stage = "download-secondary" if pool_name == "FULL_READ" else "download-reserve"
    paths.ensure_stage(stage)
    log_path = paths.full_read_download_log if pool_name == "FULL_READ" else paths.reserve_download_log
    logger = setup_logger(f"download.{pool_name.lower()}.{venue}.{year}", log_path)
    source_config = load_yaml(source_config_path)
    http_config = source_config.get("http", {})
    session = _secondary_session(http_config)
    timeout = float(http_config.get("timeout_seconds", 30))
    request_interval = float(http_config.get("request_interval_seconds", 0.6))

    used_filenames: dict[str, str] = {}
    for row in candidates.to_dict(orient="records"):
        paper_id = clean_cell(row["Paper_ID（论文编号）"])
        title = clean_cell(row["Title（标题）"])
        pdf_url = clean_cell(row["PDF_URL（官方PDF链接）"])
        read_order = clean_cell(row.get(READ_ORDER_COLUMN))
        destination = _secondary_destination(paths, pool_name, paper_id, title, read_order)
        filename_key = destination.name.casefold()
        if filename_key in used_filenames and used_filenames[filename_key] != paper_id:
            raise ValueError(
                f"Sanitized {pool_name} filenames collide for Paper_ID "
                f"{used_filenames[filename_key]} and {paper_id}: "
                f"{destination.name}"
            )
        used_filenames[filename_key] = paper_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        legacy_flat_path = paths.full_read_pdf_dir / destination.name
        status = "FAILED"
        error = ""
        size = 0

        if pool_name == "FULL_READ" and legacy_flat_path.exists() and legacy_flat_path != destination:
            if destination.exists():
                raise FileExistsError(
                    f"Both flat and organized PDFs exist for Paper_ID {paper_id}; refusing to create duplicates"
                )
            if not _valid_pdf(legacy_flat_path):
                raise ValueError(f"Legacy flat file is not a valid PDF: {legacy_flat_path}")
            legacy_flat_path.replace(destination)
            status = "SKIPPED_EXISTS"
            size = destination.stat().st_size
            logger.info("PDF MOVED_AND_SKIPPED: %s", destination)
        elif destination.exists() and not force:
            if _valid_pdf(destination):
                status = "SKIPPED_EXISTS"
                size = destination.stat().st_size
                logger.info("PDF SKIPPED_EXISTS: %s", destination)
            else:
                status = "INVALID_EXISTING_FILE"
                error = "Existing file is not a valid PDF and was not overwritten"
                logger.error("PDF INVALID_EXISTING_FILE: %s", destination)
        else:
            try:
                status, size = download_pdf_with_retries(
                    session, pdf_url, destination, timeout, force=force
                )
                if status == "ALREADY_EXISTS":
                    status = "SKIPPED_EXISTS"
                logger.info("PDF %s: %s", status, destination)
                if status == "DOWNLOADED":
                    time.sleep(request_interval)
            except Exception as exc:
                status = "FAILED"
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("%s PDF failed after bounded retries: %s", pool_name, pdf_url)

        local_path = str(destination.resolve()) if status in {"DOWNLOADED", "SKIPPED_EXISTS"} else ""
        manifest_by_id[paper_id] = {
            "Paper_ID（论文编号）": paper_id,
            "Title（标题）": title,
            SECONDARY_DECISION_COLUMN: pool_name,
            READ_ORDER_COLUMN: read_order,
            "PDF_URL（官方PDF链接）": pdf_url,
            "Download_Status（下载状态）": status,
            "Local_PDF_Path（本地PDF路径）": local_path,
            "File_Size_Bytes（文件大小_字节）": size,
            "Error（错误信息）": error,
        }

    manifest_frame = pd.DataFrame(list(manifest_by_id.values())).reindex(
        columns=SECONDARY_MANIFEST_COLUMNS, fill_value=""
    )
    write_csv(manifest_frame, manifest_path, SECONDARY_MANIFEST_COLUMNS)
    logger.info(
        "%s download stage complete: selected=%d retry_failed=%s",
        pool_name,
        len(candidates),
        retry_failed,
    )
    return manifest_frame


def _read_kd_review_manifest(manifest_path: Path, required: bool = False) -> pd.DataFrame:
    """Read the Assignment-driven KD review manifest without changing destinations."""
    if not manifest_path.exists():
        if required:
            raise FileNotFoundError(f"KD review PDF manifest not found: {manifest_path}")
        return pd.DataFrame(columns=KD_REVIEW_MANIFEST_COLUMNS)
    frame = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing = [column for column in KD_REVIEW_MANIFEST_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("Existing KD review manifest is missing columns: " + ", ".join(missing))
    if frame[PAPER_ID_COLUMN].duplicated().any():
        raise ValueError("Existing KD review manifest contains duplicate Paper_ID values")
    return frame


def _write_kd_review_download_report(
    paths: ProjectPaths,
    manifest: pd.DataFrame,
    assignment: pd.DataFrame,
) -> None:
    """Write a compact human status file, never a copy of the paper table."""
    success = {"DOWNLOADED", "SKIPPED_EXISTS"}
    status_by_id = {
        clean_cell(row[PAPER_ID_COLUMN]): clean_cell(row["Download_Status（下载状态）"])
        for row in manifest.to_dict(orient="records")
    }
    expected_ids = [clean_cell(value) for value in assignment[PAPER_ID_COLUMN]]
    downloaded = sum(status_by_id.get(paper_id) in success for paper_id in expected_ids)
    failed_rows = [
        row
        for row in manifest.to_dict(orient="records")
        if clean_cell(row[PAPER_ID_COLUMN]) in set(expected_ids)
        and clean_cell(row["Download_Status（下载状态）"]) == "FAILED"
    ]
    missing = len(expected_ids) - sum(paper_id in status_by_id for paper_id in expected_ids)

    class_lines: list[str] = []
    for secondary_class in ("KD_Method_Centric", "Other_KD"):
        class_ids = [
            clean_cell(row[PAPER_ID_COLUMN])
            for row in assignment.to_dict(orient="records")
            if clean_cell(row[SECONDARY_CLASS_COLUMN]) == secondary_class
        ]
        class_downloaded = sum(status_by_id.get(paper_id) in success for paper_id in class_ids)
        class_lines.append(f"{secondary_class}: {class_downloaded}/{len(class_ids)}")

    warning = bool(failed_rows or missing)
    lines = [
        "# WARNING" if warning else f"# {paths.normalized_venue} {paths.year} KD Review Download",
        "",
        f"Expected: {len(expected_ids)}",
        f"Downloaded: {downloaded}",
        f"Failed: {len(failed_rows)}",
        f"Missing: {missing}",
        "",
        *class_lines,
    ]
    if failed_rows:
        lines.extend(["", "Failed:"])
        lines.extend(
            f"- {clean_cell(row[PAPER_ID_COLUMN])}: {clean_cell(row['Title（标题）'])}"
            for row in failed_rows[:20]
        )
        if len(failed_rows) > 20:
            lines.append(f"- ... and {len(failed_rows) - 20} more")
        lines.extend(["", "Action:", "Run download-secondary --retry-failed"])
    lines.extend(["", f"Status: {'WARNING' if warning else 'OK'}"])
    paths.kd_review_download_report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _planned_kd_review_rows(paths: ProjectPaths, assignment: pd.DataFrame) -> list[dict[str, object]]:
    """Plan every final path before any directory creation or network request."""
    used_filenames: set[str] = set()
    planned: list[dict[str, object]] = []
    for row in assignment.to_dict(orient="records"):
        paper_id = clean_cell(row[PAPER_ID_COLUMN])
        title = clean_cell(row[ASSIGNMENT_TITLE_COLUMN])
        secondary_class = clean_cell(row[SECONDARY_CLASS_COLUMN])
        assistant = clean_cell(row[ASSISTANT_GROUP_COLUMN])
        destination_dir = paths.kd_review_assistant_dir(secondary_class, assistant)
        filename, collision = plan_title_pdf_filename(
            title,
            paper_id,
            destination_dir,
            used_filenames,
        )
        planned.append(
            {
                **row,
                "_saved_filename": filename,
                "_collision": collision,
                "_destination": destination_dir / filename,
            }
        )
    return planned


def _validate_manifest_destination(paths: ProjectPaths, row: dict[str, object]) -> Path:
    """Ensure retry-failed uses the exact previously recorded final Assignment path."""
    paper_id = clean_cell(row[PAPER_ID_COLUMN])
    secondary_class = clean_cell(row[SECONDARY_CLASS_COLUMN])
    assistant = clean_cell(row[ASSISTANT_GROUP_COLUMN])
    filename = clean_cell(row["Saved_Filename（保存文件名）"])
    recorded = clean_cell(row["Local_PDF_Path（本地PDF路径）"])
    if not filename or not recorded:
        raise ValueError(f"FAILED manifest row lacks Saved_Filename or Local_PDF_Path: {paper_id}")
    expected = (paths.kd_review_assistant_dir(secondary_class, assistant) / filename).resolve()
    actual = Path(recorded).resolve()
    if actual != expected:
        raise ValueError(f"FAILED manifest target does not match its Assignment location: {paper_id}")
    return actual


def _download_assigned_kd_review(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
) -> pd.DataFrame:
    """Validate Assignment, then download directly to each class/assistant directory."""
    paths = ProjectPaths(root, venue.upper(), year)
    validation = validate_kd_review_assignment(root, venue, year)
    assignment = validation.assignment
    planned = _planned_kd_review_rows(paths, assignment)
    assignment_by_id = {
        clean_cell(row[PAPER_ID_COLUMN]): row for row in planned
    }
    previous = _read_kd_review_manifest(paths.kd_review_manifest_csv, required=retry_failed)
    manifest_by_id = {
        clean_cell(row[PAPER_ID_COLUMN]): row for row in previous.to_dict(orient="records")
    }

    if retry_failed:
        candidates: list[dict[str, object]] = []
        for manifest_row in previous.to_dict(orient="records"):
            if clean_cell(manifest_row["Download_Status（下载状态）"]) != "FAILED":
                continue
            paper_id = clean_cell(manifest_row[PAPER_ID_COLUMN])
            if paper_id not in assignment_by_id:
                raise ValueError(f"FAILED manifest Paper_ID is missing from Assignment: {paper_id}")
            assignment_row = assignment_by_id[paper_id]
            for column in (SECONDARY_CLASS_COLUMN, ASSISTANT_GROUP_COLUMN, ASSISTANT_ORDER_COLUMN, BATCH_ID_COLUMN):
                if clean_cell(manifest_row[column]) != clean_cell(assignment_row[column]):
                    raise ValueError(f"FAILED manifest Assignment fields changed for Paper_ID {paper_id}")
            destination = _validate_manifest_destination(paths, manifest_row)
            candidates.append(
                {
                    **assignment_row,
                    "_saved_filename": clean_cell(manifest_row["Saved_Filename（保存文件名）"]),
                    "_collision": clean_cell(manifest_row["Filename_Collision（文件名冲突）"]).upper() == "TRUE",
                    "_destination": destination,
                }
            )
    else:
        for paper_id, manifest_row in manifest_by_id.items():
            if paper_id not in assignment_by_id:
                raise ValueError(f"Manifest Paper_ID is missing from current Assignment: {paper_id}")
            planned_row = assignment_by_id[paper_id]
            planned_destination = Path(planned_row["_destination"]).resolve()
            recorded_destination = clean_cell(manifest_row["Local_PDF_Path（本地PDF路径）"])
            same_assignment = all(
                clean_cell(manifest_row[column]) == clean_cell(planned_row[column])
                for column in (SECONDARY_CLASS_COLUMN, ASSISTANT_GROUP_COLUMN, ASSISTANT_ORDER_COLUMN, BATCH_ID_COLUMN)
            )
            if (
                not same_assignment
                or clean_cell(manifest_row["Saved_Filename（保存文件名）"])
                != clean_cell(planned_row["_saved_filename"])
                or not recorded_destination
                or Path(recorded_destination).resolve() != planned_destination
            ):
                raise ValueError(
                    f"Existing Manifest target differs from current Assignment for Paper_ID {paper_id}; "
                    "refusing to create a duplicate"
                )
        candidates = planned

    if limit is not None:
        candidates = candidates[:limit]

    paths.ensure_stage("download-secondary")
    logger = setup_logger(f"download.full_read.{venue}.{year}", paths.full_read_download_log)
    source_config = load_yaml(source_config_path)
    http_config = source_config.get("http", {})
    session = _secondary_session(http_config)
    timeout = float(http_config.get("timeout_seconds", 30))
    request_interval = float(http_config.get("request_interval_seconds", 0.6))

    for row in candidates:
        paper_id = clean_cell(row[PAPER_ID_COLUMN])
        title = clean_cell(row[ASSIGNMENT_TITLE_COLUMN])
        pdf_url = clean_cell(row[ASSIGNMENT_PDF_URL_COLUMN])
        destination = Path(row["_destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        status = "FAILED"
        failure_reason = ""
        size = 0

        if destination.exists() and not force:
            if _valid_pdf(destination):
                status = "SKIPPED_EXISTS"
                size = destination.stat().st_size
                logger.info("PDF SKIPPED_EXISTS: %s", destination)
            else:
                status = "FAILED"
                failure_reason = "Existing file is not a valid PDF and was not overwritten"
                logger.error("PDF INVALID_EXISTING_FILE: %s", destination)
        else:
            try:
                status, size = download_pdf_with_retries(
                    session,
                    pdf_url,
                    destination,
                    timeout,
                    force=force,
                )
                if status == "ALREADY_EXISTS":
                    status = "SKIPPED_EXISTS"
                logger.info("PDF %s: %s", status, destination)
                if status == "DOWNLOADED":
                    time.sleep(request_interval)
            except Exception as exc:
                status = "FAILED"
                failure_reason = f"{type(exc).__name__}: {exc}"
                logger.exception("KD review PDF failed after bounded retries: %s", pdf_url)

        manifest_by_id[paper_id] = {
            PAPER_ID_COLUMN: paper_id,
            "Title（标题）": title,
            SECONDARY_CLASS_COLUMN: clean_cell(row[SECONDARY_CLASS_COLUMN]),
            ASSISTANT_GROUP_COLUMN: clean_cell(row[ASSISTANT_GROUP_COLUMN]),
            ASSISTANT_ORDER_COLUMN: clean_cell(row[ASSISTANT_ORDER_COLUMN]),
            BATCH_ID_COLUMN: clean_cell(row[BATCH_ID_COLUMN]),
            "PDF_URL（官方PDF链接）": pdf_url,
            "Original_Title（原始标题）": title,
            "Saved_Filename（保存文件名）": clean_cell(row["_saved_filename"]),
            "Filename_Collision（文件名冲突）": "TRUE" if row["_collision"] else "FALSE",
            "Local_PDF_Path（本地PDF路径）": str(destination.resolve()),
            "Download_Status（下载状态）": status,
            "File_Size_Bytes（文件大小_字节）": size,
            "Failure_Reason（失败原因）": failure_reason,
        }
        manifest_frame = pd.DataFrame(list(manifest_by_id.values())).reindex(
            columns=KD_REVIEW_MANIFEST_COLUMNS,
            fill_value="",
        )
        write_csv(manifest_frame, paths.kd_review_manifest_csv, KD_REVIEW_MANIFEST_COLUMNS)

    manifest_frame = pd.DataFrame(list(manifest_by_id.values())).reindex(
        columns=KD_REVIEW_MANIFEST_COLUMNS,
        fill_value="",
    )
    write_csv(manifest_frame, paths.kd_review_manifest_csv, KD_REVIEW_MANIFEST_COLUMNS)
    _write_kd_review_download_report(paths, manifest_frame, assignment)
    logger.info(
        "KD review Assignment download complete: selected=%d retry_failed=%s",
        len(candidates),
        retry_failed,
    )
    return manifest_frame


def download_secondary_candidates(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
) -> pd.DataFrame:
    """Download both readable KD classes directly into Assignment-defined folders."""
    paths = ProjectPaths(root, venue.upper(), year)
    new_schema_present = any(
        path.exists()
        for path in (
            paths.kd_method_centric_csv,
            paths.other_kd_csv,
            paths.kd_review_assignment_csv,
        )
    )
    if not new_schema_present and paths.full_read_csv.exists():
        return _download_secondary_pool(
            root,
            venue,
            year,
            source_config_path,
            "FULL_READ",
            limit,
            force,
            retry_failed,
        )
    return _download_assigned_kd_review(
        root,
        venue,
        year,
        source_config_path,
        limit,
        force,
        retry_failed,
    )


def download_reserve_candidates(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
) -> pd.DataFrame:
    """Download only RESERVE.csv papers into the independent RESERVE pool."""
    return _download_secondary_pool(
        root,
        venue,
        year,
        source_config_path,
        "RESERVE",
        limit,
        force,
        retry_failed,
    )


def organize_existing_secondary_pdfs(root: Path, venue: str, year: int) -> dict[str, int]:
    """Transactionally move flat successful PDFs into Read_Order folders and update the manifest."""
    paths = ProjectPaths(root, venue.upper(), year)
    full_read = load_full_read_csv(paths.full_read_csv, require_read_order=True)
    input_by_id = {
        clean_cell(row["Paper_ID（论文编号）"]): row for row in full_read.to_dict(orient="records")
    }
    manifest_path = paths.full_read_manifest_csv
    manifest = _read_secondary_manifest(manifest_path, "FULL_READ", required=True)
    # Fail before moving if another application has locked the manifest for writing.
    with manifest_path.open("r+b"):
        pass

    paths.ensure_stage("organize")
    logger = setup_logger(f"download.secondary.{venue}.{year}", paths.full_read_download_log)
    full_read_root = paths.full_read_pdf_dir.resolve()
    success_statuses = {"DOWNLOADED", "SKIPPED_EXISTS"}
    plans: list[tuple[Path, Path, str]] = []
    planned_root_sources: set[Path] = set()
    already_organized = 0
    updated_rows: list[dict[str, object]] = []

    for row in manifest.to_dict(orient="records"):
        paper_id = clean_cell(row["Paper_ID（论文编号）"])
        if paper_id not in input_by_id:
            raise ValueError(f"Manifest Paper_ID missing from FULL_READ.csv: {paper_id}")
        input_row = input_by_id[paper_id]
        title = clean_cell(input_row["Title（标题）"])
        read_order = clean_cell(input_row[READ_ORDER_COLUMN])
        expected_name = build_secondary_pdf_filename(paper_id, title)
        target = _secondary_destination(paths, "FULL_READ", paper_id, title, read_order).resolve()
        status = clean_cell(row["Download_Status（下载状态）"])
        updated = dict(row)
        updated[READ_ORDER_COLUMN] = read_order

        if status in success_statuses:
            recorded_path = clean_cell(row.get("Local_PDF_Path（本地PDF路径）"))
            source = Path(recorded_path).resolve() if recorded_path else full_read_root / expected_name
            if not source.exists():
                matches = [
                    candidate.resolve()
                    for candidate in full_read_root.glob("*.pdf")
                    if candidate.name.casefold().startswith(sanitize_filename(paper_id, 60).casefold() + "_")
                ]
                if len(matches) != 1:
                    raise FileNotFoundError(
                        f"Cannot reliably match Paper_ID {paper_id} to one flat PDF; matches={len(matches)}"
                    )
                source = matches[0]
            if not source.name.casefold().startswith(sanitize_filename(paper_id, 60).casefold() + "_"):
                raise ValueError(f"Manifest PDF filename does not match Paper_ID {paper_id}: {source.name}")
            if not _valid_pdf(source):
                raise ValueError(f"Existing PDF is invalid for Paper_ID {paper_id}: {source}")
            try:
                source.relative_to(full_read_root)
                target.relative_to(full_read_root)
            except ValueError as exc:
                raise ValueError(f"PDF path escapes FULL_READ directory for Paper_ID {paper_id}") from exc
            if source == target:
                already_organized += 1
            elif source.parent == full_read_root:
                if target.exists():
                    raise FileExistsError(f"Target already exists for Paper_ID {paper_id}: {target}")
                plans.append((source, target, read_order))
                planned_root_sources.add(source)
            else:
                raise ValueError(f"PDF is in an unexpected directory for Paper_ID {paper_id}: {source}")
            updated["Local_PDF_Path（本地PDF路径）"] = str(target)
            updated["File_Size_Bytes（文件大小_字节）"] = source.stat().st_size
        updated_rows.append(updated)

    unmatched = {path.resolve() for path in full_read_root.glob("*.pdf")} - planned_root_sources
    if unmatched:
        raise ValueError(
            "Unmatched flat PDFs found; no files were moved: "
            + ", ".join(path.name for path in sorted(unmatched, key=lambda item: str(item).casefold()))
        )

    moved: list[tuple[Path, Path]] = []
    temp_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    try:
        for source, target, _ in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append((source, target))
        updated_manifest = pd.DataFrame(updated_rows).reindex(
            columns=SECONDARY_MANIFEST_COLUMNS, fill_value=""
        )
        write_csv(updated_manifest, temp_manifest, SECONDARY_MANIFEST_COLUMNS)
        temp_manifest.replace(manifest_path)
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                target.replace(source)
        if temp_manifest.exists():
            temp_manifest.unlink()
        raise

    counts = {
        "Read_First": sum(1 for _, _, order in plans if order == READ_FIRST),
        "Read_Normal": sum(1 for _, _, order in plans if order == READ_NORMAL),
        "Already_Organized": already_organized,
        "Moved_Total": len(plans),
    }
    logger.info("Organized existing FULL_READ PDFs: %s", counts)
    return counts
