"""Respectful, cached, resumable CVF metadata crawler."""

from __future__ import annotations

import itertools
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .parser import ListingEntry, parse_cvf_detail, parse_cvf_listing, parse_cvf_workshop_index
from .utils import RAW_COLUMNS, ProjectPaths, clean_cell, load_yaml, setup_logger, stable_paper_id, write_csv, write_xlsx


class CachedHttpClient:
    """Single-threaded HTTP client with retry, timeout, pacing, and disk cache."""

    def __init__(self, config: dict, logger) -> None:
        self.timeout = float(config.get("timeout_seconds", 30))
        self.interval = float(config.get("request_interval_seconds", 0.6))
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.get("user_agent", "PaperSearchTool/1.0"),
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            }
        )
        retry = Retry(
            total=int(config.get("retries", 3)),
            connect=int(config.get("retries", 3)),
            read=int(config.get("retries", 3)),
            backoff_factor=float(config.get("backoff_factor", 1.0)),
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get_text(self, url: str, cache_path: Path, force: bool = False) -> tuple[str, str]:
        """Return text and source label (`CACHE` or `NETWORK`)."""
        if cache_path.exists() and not force:
            return cache_path.read_text(encoding="utf-8"), "CACHE"
        self.logger.info("GET %s", url)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        time.sleep(self.interval)
        return text, "NETWORK"


def _round_robin(groups: list[list[tuple[str, ListingEntry]]], limit: int | None) -> list[tuple[str, ListingEntry]]:
    """Select a global limit while representing each enabled track."""
    selected: list[tuple[str, ListingEntry]] = []
    for batch in itertools.zip_longest(*groups):
        for item in batch:
            if item is not None:
                selected.append(item)
                if limit is not None and len(selected) >= limit:
                    return selected
    return selected


def _existing_by_url(path: Path) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    if not path.exists():
        return [], {}
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    rows = frame.to_dict(orient="records")
    return rows, {clean_cell(row.get("Official_URL（官方论文页面）")): row for row in rows}


def crawl_cvf(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    limit: int | None = None,
    force: bool = False,
    title_query: str | None = None,
) -> pd.DataFrame:
    """Crawl enabled CVF tracks, preserving previous successful rows on resume."""
    venue = venue.upper()
    paths = ProjectPaths(root, venue, year)
    paths.ensure()
    logger = setup_logger(f"crawl.{venue}.{year}", paths.logs / "crawl.log")
    config = load_yaml(source_config_path)
    try:
        venue_config = config["venues"][venue]
    except KeyError as exc:
        raise ValueError(f"Unsupported venue in source config: {venue}") from exc

    client = CachedHttpClient(config.get("http", {}), logger)
    base_url = str(venue_config["base_url"])
    track_groups: list[list[tuple[str, ListingEntry]]] = []
    listing_errors: list[dict[str, str]] = []

    for track, track_config in venue_config.get("tracks", {}).items():
        if not track_config.get("enabled", False):
            logger.info("Track disabled: %s", track)
            continue
        listing_url = urljoin(base_url, str(track_config["listing_url"]).format(year=year))
        cache_name = f"{venue}{year}_{track.replace(' ', '_')}_listing.html"
        try:
            html, source = client.get_text(listing_url, paths.cache / "lists" / cache_name, force=force)
            marker = str(track_config["detail_path_marker"]).format(year=year)
            if track_config.get("listing_type") == "workshop_index":
                prefix = str(track_config["workshop_path_prefix"]).format(year=year)
                workshops = parse_cvf_workshop_index(html, base_url, prefix)
                workshop_entries: list[tuple[str, ListingEntry]] = []
                for workshop_number, workshop in enumerate(workshops, start=1):
                    safe_number = f"{workshop_number:03d}"
                    workshop_cache = paths.cache / "lists" / f"{venue}{year}_Workshop_{safe_number}.html"
                    try:
                        workshop_html, workshop_source = client.get_text(
                            workshop.listing_url, workshop_cache, force=force
                        )
                        entries = parse_cvf_listing(workshop_html, base_url, marker)
                        if title_query:
                            query = title_query.casefold()
                            entries = [entry for entry in entries if query in entry.title.casefold()]
                        workshop_entries.extend((f"Workshop: {workshop.name}", entry) for entry in entries)
                        logger.info(
                            "Workshop listing parsed: name=%s count=%d source=%s",
                            workshop.name,
                            len(entries),
                            workshop_source,
                        )
                        if limit is not None and len(workshop_entries) >= limit:
                            break
                    except Exception as exc:
                        listing_errors.append(
                            {
                                "Track（论文轨道）": f"Workshop: {workshop.name}",
                                "Official_URL（官方论文页面）": workshop.listing_url,
                                "Crawl_Error（抓取错误）": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        logger.exception("Workshop listing failed: %s", workshop.listing_url)
                if not workshop_entries:
                    raise ValueError("No matching workshop paper entries found")
                track_groups.append(workshop_entries)
                logger.info("Workshop hub parsed: workshops=%d papers=%d", len(workshops), len(workshop_entries))
            else:
                entries = parse_cvf_listing(html, base_url, marker)
                if title_query:
                    query = title_query.casefold()
                    entries = [entry for entry in entries if query in entry.title.casefold()]
                logger.info("Listing parsed: track=%s count=%d source=%s", track, len(entries), source)
                if not entries:
                    raise ValueError("No matching paper entries found")
                track_groups.append([(track, entry) for entry in entries])
        except Exception as exc:  # keep other tracks running
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("Listing failed: track=%s url=%s", track, listing_url)
            listing_errors.append({"Track（论文轨道）": track, "Official_URL（官方论文页面）": listing_url, "Crawl_Error（抓取错误）": message})

    selected = _round_robin(track_groups, limit)
    previous_rows, previous_by_url = _existing_by_url(paths.raw_csv)
    updated_by_url: dict[str, dict[str, object]] = {}
    detail_failures: list[dict[str, str]] = []

    for index, (track, entry) in enumerate(selected, start=1):
        old = previous_by_url.get(entry.official_url)
        if old and clean_cell(old.get("Crawl_Status（抓取状态）")) == "SUCCESS" and not force:
            logger.info("Resume %d/%d from raw table: %s", index, len(selected), entry.title)
            updated_by_url[entry.official_url] = old
            continue

        paper_id = stable_paper_id(venue, year, track, entry.official_url)
        detail_cache = paths.cache / "details" / f"{paper_id}.html"
        try:
            html, source = client.get_text(entry.official_url, detail_cache, force=force)
            detail = parse_cvf_detail(html, entry.official_url)
            missing = [key for key in ("title", "authors", "abstract", "pdf_url", "bibtex") if not detail[key]]
            status = "SUCCESS" if not missing else "PARTIAL"
            error = "" if not missing else f"Missing fields: {', '.join(missing)}"
            row: dict[str, object] = {
                "Paper_ID（论文编号）": paper_id,
                "Title（标题）": detail["title"] or entry.title,
                "Authors（作者）": detail["authors"],
                "Abstract（摘要）": detail["abstract"],
                "Venue（会议/期刊）": venue,
                "Year（年份）": year,
                "Track（论文轨道）": track,
                "Official_URL（官方论文页面）": entry.official_url,
                "PDF_URL（官方PDF链接）": detail["pdf_url"],
                "BibTeX（BibTeX信息）": detail["bibtex"],
                "Crawl_Status（抓取状态）": status,
                "Crawl_Error（抓取错误）": error,
                "Notes（备注）": f"Detail source: {source}",
            }
            updated_by_url[entry.official_url] = row
            if status != "SUCCESS":
                detail_failures.append(
                    {
                        "Track（论文轨道）": track,
                        "Official_URL（官方论文页面）": entry.official_url,
                        "Crawl_Error（抓取错误）": error,
                    }
                )
            logger.info("Parsed %d/%d status=%s title=%s", index, len(selected), status, row["Title（标题）"])
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("Detail failed %d/%d: %s", index, len(selected), entry.official_url)
            updated_by_url[entry.official_url] = {
                "Paper_ID（论文编号）": paper_id,
                "Title（标题）": entry.title,
                "Authors（作者）": "",
                "Abstract（摘要）": "",
                "Venue（会议/期刊）": venue,
                "Year（年份）": year,
                "Track（论文轨道）": track,
                "Official_URL（官方论文页面）": entry.official_url,
                "PDF_URL（官方PDF链接）": "",
                "BibTeX（BibTeX信息）": "",
                "Crawl_Status（抓取状态）": "FAILED",
                "Crawl_Error（抓取错误）": message,
                "Notes（备注）": "Detail fetch/parse failed; retained for retry and manual review.",
            }
            detail_failures.append(
                {"Track（论文轨道）": track, "Official_URL（官方论文页面）": entry.official_url, "Crawl_Error（抓取错误）": message}
            )

    merged_by_url = {clean_cell(row.get("Official_URL（官方论文页面）")): row for row in previous_rows}
    merged_by_url.update(updated_by_url)
    merged_rows = list(merged_by_url.values())
    merged_rows.sort(key=lambda row: (clean_cell(row.get("Track（论文轨道）")), clean_cell(row.get("Title（标题）"))))
    frame = pd.DataFrame(merged_rows).reindex(columns=RAW_COLUMNS, fill_value="")
    write_csv(frame, paths.raw_csv, RAW_COLUMNS)
    write_xlsx(frame, paths.raw_xlsx, RAW_COLUMNS)

    exception_columns = ["Track（论文轨道）", "Official_URL（官方论文页面）", "Crawl_Error（抓取错误）"]
    persistent_failures = [
        {
            "Track（论文轨道）": clean_cell(row.get("Track（论文轨道）")),
            "Official_URL（官方论文页面）": clean_cell(row.get("Official_URL（官方论文页面）")),
            "Crawl_Error（抓取错误）": clean_cell(row.get("Crawl_Error（抓取错误）"))
            or clean_cell(row.get("Crawl_Status（抓取状态）")),
        }
        for row in merged_rows
        if clean_cell(row.get("Crawl_Status（抓取状态）")) != "SUCCESS"
    ]
    write_csv(listing_errors + persistent_failures, paths.raw / f"{paths.stem}_Crawl_Exception_Queue.csv", exception_columns)
    logger.info("Crawl complete: selected=%d total_saved=%d exceptions=%d", len(selected), len(frame), len(listing_errors) + len(persistent_failures))
    return frame
