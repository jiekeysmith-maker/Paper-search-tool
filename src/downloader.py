"""Official PDF downloader with Windows-safe filenames and a persistent manifest."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import (
    DECISION_AMBIGUOUS,
    DECISION_KEEP,
    DECISION_MAYBE,
    MANIFEST_COLUMNS,
    SCREENING_COLUMNS,
    ProjectPaths,
    clean_cell,
    load_yaml,
    setup_logger,
    write_csv,
    write_xlsx,
)


SECONDARY_DECISION_COLUMN = "Secondary_Decision（二次筛选结果）"
SECONDARY_REQUIRED_COLUMNS = [
    "Paper_ID（论文编号）",
    "Title（标题）",
    "PDF_URL（官方PDF链接）",
    SECONDARY_DECISION_COLUMN,
]
SECONDARY_MANIFEST_COLUMNS = [
    "Paper_ID（论文编号）",
    "Title（标题）",
    SECONDARY_DECISION_COLUMN,
    "PDF_URL（官方PDF链接）",
    "Download_Status（下载状态）",
    "Local_PDF_Path（本地PDF路径）",
    "File_Size_Bytes（文件大小_字节）",
    "Error（错误信息）",
]


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
    """Build the FULL_READ `<Paper_ID>_<Sanitized_Title>.pdf` filename."""
    safe_id = sanitize_filename(paper_id, 60)
    budget = max(20, max_length - len(safe_id) - len("_.pdf"))
    safe_title = sanitize_filename(title, budget)
    return f"{safe_id}_{safe_title}.pdf"


def validate_full_read_frame(frame: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    """Validate and normalize a topic-controller FULL_READ table before any download."""
    missing = [column for column in SECONDARY_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"FULL_READ.csv missing required columns: {', '.join(missing)}; file={source_path}")

    validated = frame.copy()
    for column in SECONDARY_REQUIRED_COLUMNS:
        validated[column] = validated[column].map(clean_cell).str.strip()

    invalid_decisions = sorted(
        value for value in validated[SECONDARY_DECISION_COLUMN].unique() if value != "FULL_READ"
    )
    if invalid_decisions:
        raise ValueError(
            "FULL_READ.csv contains non-FULL_READ Secondary_Decision values: "
            + ", ".join(repr(value) for value in invalid_decisions)
        )

    empty_ids = validated.index[validated["Paper_ID（论文编号）"] == ""].tolist()
    if empty_ids:
        raise ValueError(f"FULL_READ.csv contains empty Paper_ID at data rows: {[index + 2 for index in empty_ids]}")

    duplicates = sorted(
        validated.loc[
            validated["Paper_ID（论文编号）"].duplicated(keep=False), "Paper_ID（论文编号）"
        ].unique()
    )
    if duplicates:
        raise ValueError(f"FULL_READ.csv contains duplicate Paper_ID values: {', '.join(duplicates)}")

    empty_urls = validated.index[validated["PDF_URL（官方PDF链接）"] == ""].tolist()
    if empty_urls:
        raise ValueError(f"FULL_READ.csv contains empty PDF_URL at data rows: {[index + 2 for index in empty_urls]}")

    return validated


def load_full_read_csv(source_path: Path) -> pd.DataFrame:
    """Read the fixed FULL_READ CSV and fail before download on structural errors."""
    if not source_path.exists():
        raise FileNotFoundError(f"FULL_READ.csv not found: {source_path}")
    frame = pd.read_csv(source_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    return validate_full_read_frame(frame, source_path)


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
    for decision, filename in (
        (DECISION_KEEP, "RULE_KEEP.csv"),
        (DECISION_MAYBE, "RULE_MAYBE.csv"),
        (DECISION_AMBIGUOUS, "RULE_AMBIGUOUS.csv"),
    ):
        write_csv(frame[frame["Decision（筛选决定）"] == decision], paths.screening / filename, SCREENING_COLUMNS)


def download_candidates(
    root: Path,
    venue: str,
    year: int,
    rules_path: Path,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Download configured candidate categories and update tables plus manifest."""
    paths = ProjectPaths(root, venue.upper(), year)
    paths.ensure()
    logger = setup_logger(f"download.{venue}.{year}", paths.logs / "download.log")
    if not paths.screening_csv.exists():
        raise FileNotFoundError(f"Screening results not found: {paths.screening_csv}")
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

    manifest_path = paths.manifests / f"{paths.stem}_PDF_Manifest.csv"
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
        folder = paths.pdfs / _folder_for_decision(decision)
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


def download_secondary_candidates(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Download only topic-controller FULL_READ papers into the isolated FULL_READ folder."""
    paths = ProjectPaths(root, venue.upper(), year)
    paths.ensure()
    logger = setup_logger(f"download.secondary.{venue}.{year}", paths.logs / "download_secondary.log")
    source_path = paths.output_root / "secondary_screening" / "FULL_READ.csv"
    candidates = load_full_read_csv(source_path)
    if limit is not None:
        candidates = candidates.head(limit)

    source_config = load_yaml(source_config_path)
    http_config = source_config.get("http", {})
    session = _session(http_config)
    timeout = float(http_config.get("timeout_seconds", 30))
    request_interval = float(http_config.get("request_interval_seconds", 0.6))
    output_folder = paths.pdfs / "FULL_READ"
    output_folder.mkdir(parents=True, exist_ok=True)
    manifest_path = paths.manifests / f"{paths.stem}_FULL_READ_PDF_Manifest.csv"

    manifest_by_id: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        previous = pd.read_csv(manifest_path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        if "Paper_ID（论文编号）" not in previous.columns:
            raise ValueError(f"Existing FULL_READ manifest is missing Paper_ID column: {manifest_path}")
        manifest_by_id = {
            clean_cell(row["Paper_ID（论文编号）"]): row for row in previous.to_dict(orient="records")
        }

    used_filenames: dict[str, str] = {}
    for row in candidates.to_dict(orient="records"):
        paper_id = clean_cell(row["Paper_ID（论文编号）"])
        title = clean_cell(row["Title（标题）"])
        pdf_url = clean_cell(row["PDF_URL（官方PDF链接）"])
        filename = build_secondary_pdf_filename(paper_id, title)
        filename_key = filename.casefold()
        if filename_key in used_filenames and used_filenames[filename_key] != paper_id:
            raise ValueError(
                f"Sanitized FULL_READ filenames collide for Paper_ID {used_filenames[filename_key]} and {paper_id}: {filename}"
            )
        used_filenames[filename_key] = paper_id
        destination = output_folder / filename
        status = "FAILED"
        error = ""
        size = 0

        if destination.exists() and not force:
            with destination.open("rb") as handle:
                valid_existing = destination.stat().st_size > 4 and handle.read(5) == b"%PDF-"
            if valid_existing:
                status = "SKIPPED_EXISTS"
                size = destination.stat().st_size
                logger.info("PDF SKIPPED_EXISTS: %s", destination)
            else:
                status = "INVALID_EXISTING_FILE"
                error = "Existing file is not a valid PDF and was not overwritten"
                logger.error("PDF INVALID_EXISTING_FILE: %s", destination)
        else:
            try:
                status, size = download_pdf_file(session, pdf_url, destination, timeout, force=force)
                if status == "ALREADY_EXISTS":
                    status = "SKIPPED_EXISTS"
                logger.info("PDF %s: %s", status, destination)
                if status == "DOWNLOADED":
                    time.sleep(request_interval)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("FULL_READ PDF failed: %s", pdf_url)

        local_path = str(destination.resolve()) if status in {"DOWNLOADED", "SKIPPED_EXISTS"} else ""
        manifest_by_id[paper_id] = {
            "Paper_ID（论文编号）": paper_id,
            "Title（标题）": title,
            SECONDARY_DECISION_COLUMN: "FULL_READ",
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
    logger.info("Secondary download stage complete: selected=%d", len(candidates))
    return manifest_frame
