"""Respectful, cached, resumable CVF metadata crawler."""

from __future__ import annotations

import itertools
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .parser import (
    ListingEntry,
    parse_cvf_day_links,
    parse_cvf_detail,
    parse_cvf_listing,
    parse_cvf_workshop_index,
)
from .paths import ProjectPaths
from .utils import RAW_COLUMNS, clean_cell, load_yaml, setup_logger, stable_paper_id, write_csv, write_xlsx


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
    refresh_listing: bool = False,
    title_query: str | None = None,
) -> pd.DataFrame:
    """Crawl enabled CVF tracks, preserving previous successful rows on resume.

    ``force`` refreshes both listing and detail pages. ``refresh_listing`` only
    refreshes listing-level pages, so successful detail rows remain reusable.
    """
    venue = venue.upper()
    paths = ProjectPaths(root, venue, year)
    paths.ensure_stage("crawl")
    logger = setup_logger(f"crawl.{venue}.{year}", paths.crawl_log)
    config = load_yaml(source_config_path)
    try:
        venue_config = config["venues"][venue]
    except KeyError as exc:
        raise ValueError(f"Unsupported venue in source config: {venue}") from exc

    client = CachedHttpClient(config.get("http", {}), logger)
    base_url = str(venue_config["base_url"])
    force_listing = force or refresh_listing
    previous_source = paths.source_raw_csv()
    previous_rows, previous_by_url = _existing_by_url(previous_source)
    track_groups: list[list[tuple[str, ListingEntry]]] = []
    listing_errors: list[dict[str, str]] = []
    enabled_tracks: set[str] = set()
    current_listing_urls: set[str] = set()
    listed_entries_by_url: dict[str, tuple[str, ListingEntry]] = {}
    listing_entry_count = 0
    main_day_all_count = 0
    daily_pages_discovered = 0

    for track, track_config in venue_config.get("tracks", {}).items():
        if not track_config.get("enabled", False):
            logger.info("Track disabled: %s", track)
            continue
        enabled_tracks.add(track)
        listing_url = urljoin(base_url, str(track_config["listing_url"]).format(year=year))
        cache_name = f"{venue}{year}_{track.replace(' ', '_')}_listing.html"
        try:
            html, source = client.get_text(listing_url, paths.listing_cache(cache_name), force=force_listing)
            marker = str(track_config["detail_path_marker"]).format(year=year)
            if track_config.get("listing_type") == "workshop_index":
                prefix = str(track_config["workshop_path_prefix"]).format(year=year)
                workshops = parse_cvf_workshop_index(html, base_url, prefix)
                workshop_entries: list[tuple[str, ListingEntry]] = []
                for workshop_number, workshop in enumerate(workshops, start=1):
                    safe_number = f"{workshop_number:03d}"
                    workshop_cache = paths.listing_cache(f"{venue}{year}_Workshop_{safe_number}.html")
                    try:
                        workshop_html, workshop_source = client.get_text(
                            workshop.listing_url, workshop_cache, force=force_listing
                        )
                        entries = parse_cvf_listing(workshop_html, base_url, marker)
                        listing_entry_count += len(entries)
                        current_listing_urls.update(entry.official_url for entry in entries)
                        listed_entries_by_url.update(
                            {entry.official_url: (f"Workshop: {workshop.name}", entry) for entry in entries}
                        )
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
                if venue == "CVPR" and track == "Main Conference":
                    main_day_all_count = len(entries)
                    daily_urls = parse_cvf_day_links(html, base_url, venue, year)
                    if not daily_urls:
                        parsed_listing_url = urlsplit(listing_url)
                        day_index_url = urlunsplit(
                            (
                                parsed_listing_url.scheme,
                                parsed_listing_url.netloc,
                                parsed_listing_url.path,
                                "",
                                "",
                            )
                        )
                        day_index_cache = paths.listing_cache(
                            f"{venue}{year}_{track.replace(' ', '_')}_day_index.html"
                        )
                        try:
                            day_index_html, day_index_source = client.get_text(
                                day_index_url,
                                day_index_cache,
                                force=force_listing,
                            )
                            daily_urls = parse_cvf_day_links(day_index_html, base_url, venue, year)
                            logger.info(
                                "Daily listing index parsed: count=%d source=%s url=%s",
                                len(daily_urls),
                                day_index_source,
                                day_index_url,
                            )
                            if not daily_urls:
                                listing_errors.append(
                                    {
                                        "Track（论文轨道）": "Main Conference Day Discovery",
                                        "Official_URL（官方论文页面）": day_index_url,
                                        "Crawl_Error（抓取错误）": "No valid daily listing links discovered",
                                    }
                                )
                        except Exception as exc:
                            listing_errors.append(
                                {
                                    "Track（论文轨道）": "Main Conference Day Discovery",
                                    "Official_URL（官方论文页面）": day_index_url,
                                    "Crawl_Error（抓取错误）": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            logger.exception("Daily listing discovery failed: %s", day_index_url)

                    daily_pages_discovered = len(daily_urls)
                    union_by_url = {entry.official_url: entry for entry in entries}
                    for daily_url in daily_urls:
                        day_value = parse_qs(urlsplit(daily_url).query)["day"][0]
                        daily_cache = paths.listing_cache(
                            f"{venue}{year}_{track.replace(' ', '_')}_day_{day_value}.html"
                        )
                        try:
                            daily_html, daily_source = client.get_text(
                                daily_url,
                                daily_cache,
                                force=force_listing,
                            )
                            daily_entries = parse_cvf_listing(daily_html, base_url, marker)
                            if not daily_entries:
                                raise ValueError("No Main Conference papers parsed from daily listing")
                            for daily_entry in daily_entries:
                                union_by_url.setdefault(daily_entry.official_url, daily_entry)
                            logger.info(
                                "Daily listing parsed: day=%s count=%d source=%s",
                                day_value,
                                len(daily_entries),
                                daily_source,
                            )
                        except Exception as exc:
                            listing_errors.append(
                                {
                                    "Track（论文轨道）": f"Main Conference Daily: {day_value}",
                                    "Official_URL（官方论文页面）": daily_url,
                                    "Crawl_Error（抓取错误）": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            logger.exception("Daily listing failed: %s", daily_url)
                    entries = list(union_by_url.values())
                    logger.info(
                        "Main listing union: day_all=%d daily_pages=%d unique_urls=%d",
                        main_day_all_count,
                        daily_pages_discovered,
                        len(entries),
                    )

                title_to_url: dict[str, str] = {}
                for entry in entries:
                    normalized_title = " ".join(entry.title.split()).casefold()
                    prior_url = title_to_url.get(normalized_title)
                    if prior_url and prior_url != entry.official_url:
                        listing_errors.append(
                            {
                                "Track（论文轨道）": f"{track} Duplicate Title",
                                "Official_URL（官方论文页面）": entry.official_url,
                                "Crawl_Error（抓取错误）": (
                                    "Duplicate_Title_Different_Official_URL: " + prior_url
                                ),
                            }
                        )
                    else:
                        title_to_url[normalized_title] = entry.official_url

                listing_entry_count += len(entries)
                current_listing_urls.update(entry.official_url for entry in entries)
                listed_entries_by_url.update({entry.official_url: (track, entry) for entry in entries})
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
    selected_urls = {entry.official_url for _, entry in selected}
    listing_new_urls = current_listing_urls.difference(previous_by_url)
    selected_new_urls = selected_urls.difference(previous_by_url)
    updated_by_url: dict[str, dict[str, object]] = {}
    detail_failures: list[dict[str, str]] = []
    reused_success = 0
    detail_requests = 0
    new_detail_requests = 0

    for index, (track, entry) in enumerate(selected, start=1):
        old = previous_by_url.get(entry.official_url)
        if old and clean_cell(old.get("Crawl_Status（抓取状态）")) == "SUCCESS" and not force:
            logger.info("Resume %d/%d from raw table: %s", index, len(selected), entry.title)
            updated_by_url[entry.official_url] = old
            reused_success += 1
            continue

        paper_id = stable_paper_id(venue, year, track, entry.official_url)
        detail_cache = paths.detail_cache(paper_id)
        detail_requests += 1
        if entry.official_url in selected_new_urls:
            new_detail_requests += 1
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

    if refresh_listing:
        new_entry_columns = [
            "Paper_ID（论文编号）",
            "Title（标题）",
            "Authors（作者）",
            "Abstract（摘要）",
            "Venue（会议/期刊）",
            "Year（年份）",
            "Track（论文轨道）",
            "Official_URL（官方论文页面）",
            "PDF_URL（官方PDF链接）",
            "BibTeX（BibTeX信息）",
            "Crawl_Status（抓取状态）",
        ]
        new_rows: list[dict[str, object]] = []
        for url in sorted(listing_new_urls):
            if url in updated_by_url:
                new_rows.append(updated_by_url[url])
                continue
            track, entry = listed_entries_by_url[url]
            new_rows.append(
                {
                    "Paper_ID（论文编号）": stable_paper_id(venue, year, track, url),
                    "Title（标题）": entry.title,
                    "Authors（作者）": "",
                    "Abstract（摘要）": "",
                    "Venue（会议/期刊）": venue,
                    "Year（年份）": year,
                    "Track（论文轨道）": track,
                    "Official_URL（官方论文页面）": url,
                    "PDF_URL（官方PDF链接）": "",
                    "BibTeX（BibTeX信息）": "",
                    "Crawl_Status（抓取状态）": "DISCOVERED_NOT_FETCHED",
                }
            )
        write_csv(new_rows, paths.cvf_listing_new_entries_csv, new_entry_columns)

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
    listing_missing: list[dict[str, str]] = []
    if refresh_listing and not listing_errors and limit is None and not title_query:
        for row in previous_rows:
            track = clean_cell(row.get("Track（论文轨道）"))
            in_enabled_track = track in enabled_tracks or (
                "Workshop" in enabled_tracks and track.startswith("Workshop:")
            )
            url = clean_cell(row.get("Official_URL（官方论文页面）"))
            if in_enabled_track and url and url not in current_listing_urls:
                listing_missing.append(
                    {
                        "Track（论文轨道）": track,
                        "Official_URL（官方论文页面）": url,
                        "Crawl_Error（抓取错误）": "Listing_Removed_or_Missing",
                    }
                )
                logger.warning("Listing_Removed_or_Missing: %s", url)

    exceptions = listing_errors + persistent_failures + listing_missing
    write_csv(exceptions, paths.crawl_exception_csv, exception_columns)
    logger.info(
        "Crawl complete: listing_parsed=%d previous=%d selected=%d new_urls=%d "
        "detail_requests=%d new_detail_requests=%d reused_success=%d total_saved=%d "
        "main_day_all=%d daily_pages=%d listing_missing=%d exceptions=%d",
        listing_entry_count,
        len(previous_rows),
        len(selected),
        len(listing_new_urls),
        detail_requests,
        new_detail_requests,
        reused_success,
        len(frame),
        main_day_all_count,
        daily_pages_discovered,
        len(listing_missing),
        len(exceptions),
    )
    return frame
