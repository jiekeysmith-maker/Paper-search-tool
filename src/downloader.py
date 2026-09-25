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
