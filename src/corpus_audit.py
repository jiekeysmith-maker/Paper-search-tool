"""Formal Proceedings completeness audit for a Venue-Year corpus."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd

from .crawler import CachedHttpClient
from .official_sources import (
    OFFICIAL_ACCEPTED_COLUMNS,
    OfficialPaper,
    fetch_cvf_findings_entries,
    fetch_official_discovery_papers,
    ieee_search_url,
    normalize_authors,
    normalize_title,
    parse_ieee_document_metadata,
    parse_ieee_exact_document_link,
)
from .paths import ProjectPaths
from .utils import RAW_COLUMNS, clean_cell, load_yaml, setup_logger, write_csv, write_xlsx


FORMAL_STATUS_CVF = "FORMAL_CVF_CONFIRMED"
FORMAL_STATUS_IEEE = "FORMAL_IEEE_CONFIRMED"
FORMAL_STATUS_NOT = "NOT_FORMALLY_PUBLISHED"
FORMAL_STATUS_NON_TARGET = "NON_TARGET_OFFICIAL_TRACK"
FORMAL_STATUS_UNRESOLVED = "UNRESOLVED"

COMPLETENESS_VERIFIED = "VERIFIED"
COMPLETENESS_REVIEW = "REVIEW_REQUIRED"
COMPLETENESS_ERROR = "ERROR"

AUDIT_EXACT = "EXACT_OR_NORMALIZED_MATCH"
AUDIT_PROBABLE = "PROBABLE_TITLE_VARIANT"
AUDIT_ACCEPTED_ONLY = "ACCEPTED_ONLY"
AUDIT_CVF_ONLY = "CVF_ONLY"
AUDIT_DUPLICATE_ACCEPTED = "DUPLICATE_ACCEPTED_TITLE"
AUDIT_DUPLICATE_CVF = "DUPLICATE_CVF_TITLE"

FORMAL_EXTRA_COLUMNS = [
    "Corpus_Source",
    "Formal_Publication_Status",
    "Formal_Publication_Evidence",
    "Formal_Publication_URL",
    "Original_CVF_Present",
    "Official_Accepted_Present",
    "Corpus_Audit_Notes",
]
FORMAL_CORPUS_COLUMNS = RAW_COLUMNS + FORMAL_EXTRA_COLUMNS

AUDIT_COLUMNS = [
    "Audit_Status",
    "Accepted_Title",
    "CVF_Title",
    "Accepted_Normalized_Title",
    "CVF_Normalized_Title",
    "Similarity",
    "Author_Similarity",
    "Authors",
    "Accepted_URL",
    "CVF_URL",
    "Formal_Publication_Status",
    "Formal_Publication_Evidence",
    "Formal_Publication_URL",
    "Paper_ID",
    "Resolution",
    "Notes",
]

REQUIRED_FORMAL_METADATA = [
    "Paper_ID（论文编号）",
    "Title（标题）",
    "Authors（作者）",
    "Abstract（摘要）",
    "Venue（会议/期刊）",
    "Year（年份）",
    "Track（论文轨道）",
    "Official_URL（官方论文页面）",
    "PDF_URL（官方PDF链接）",
]


@dataclass(frozen=True)
class CorpusAuditResult:
    status: str
    counts: dict[str, int]
    audit: pd.DataFrame
    formal_corpus: pd.DataFrame
    report_path: Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_supplemental_paper_id(
    venue: str, year: int, formal_source: str, identity: str
) -> str:
    """Build a deterministic, non-CVF Paper_ID for an official supplemental record."""
    material = f"{venue.upper()}|{year}|{formal_source}|{identity}".encode("utf-8")
    return f"{venue.upper()}{year}_SUPP_{hashlib.sha1(material).hexdigest()[:12].upper()}"


def _cvf_formal_evidence(row: dict[str, object], venue: str, year: int) -> tuple[str, str]:
    official_url = clean_cell(row.get("Official_URL（官方论文页面）"))
    bibtex = clean_cell(row.get("BibTeX（BibTeX信息）"))
    track = clean_cell(row.get("Track（论文轨道）"))
    row_venue = clean_cell(row.get("Venue（会议/期刊）")).upper()
    row_year = clean_cell(row.get("Year（年份）"))
    host = urlparse(official_url).hostname or ""
    required_booktitle = "proceedings of the ieee/cvf conference on computer vision and pattern recognition (cvpr)"
    valid = (
        venue.upper() == "CVPR"
        and row_venue == venue.upper()
        and row_year == str(year)
        and track == "Main Conference"
        and host.casefold() == "openaccess.thecvf.com"
        and f"/content/CVPR{year}/html/".casefold() in urlparse(official_url).path.casefold()
        and required_booktitle in bibtex.casefold()
        and re.search(rf"\byear\s*=\s*\{{\s*{year}\s*\}}", bibtex, flags=re.IGNORECASE)
    )
    if valid:
        return FORMAL_STATUS_CVF, "CVF Open Access page and BibTeX confirm CVPR Main Proceedings/year"
    return FORMAL_STATUS_UNRESOLVED, "CVF record did not satisfy formal Main Proceedings evidence checks"


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _audit_row(**values: object) -> dict[str, object]:
    return {column: values.get(column, "") for column in AUDIT_COLUMNS}


def _match_records(
    cvf: pd.DataFrame,
    accepted: list[OfficialPaper],
    venue: str,
    year: int,
    probable_threshold: float = 0.86,
) -> tuple[list[dict[str, object]], dict[str, bool], bool]:
    """Match discovery titles to CVF titles without auto-resolving fuzzy candidates."""
    cvf_rows = cvf.to_dict(orient="records")
    cvf_norm_groups: dict[str, list[int]] = {}
    for index, row in enumerate(cvf_rows):
        cvf_norm_groups.setdefault(normalize_title(row.get("Title（标题）", "")), []).append(index)
    accepted_norm_groups: dict[str, list[int]] = {}
    for index, paper in enumerate(accepted):
        accepted_norm_groups.setdefault(normalize_title(paper.title), []).append(index)

    records: list[dict[str, object]] = []
    accepted_presence: dict[str, bool] = {
        clean_cell(row.get("Paper_ID（论文编号）")): False for row in cvf_rows
    }
    used_accepted: set[int] = set()
    used_cvf: set[int] = set()
    structural_error = False

    for normalized, indices in accepted_norm_groups.items():
        if normalized and len(indices) > 1:
            structural_error = True
            for idx in indices:
                paper = accepted[idx]
                records.append(
                    _audit_row(
                        Audit_Status=AUDIT_DUPLICATE_ACCEPTED,
                        Accepted_Title=paper.title,
                        Accepted_Normalized_Title=normalized,
                        Authors=paper.authors,
                        Accepted_URL=paper.official_url,
                        Formal_Publication_Status=FORMAL_STATUS_UNRESOLVED,
                        Resolution="ERROR_DUPLICATE",
                        Notes="Duplicate normalized title in official discovery source.",
                    )
                )
                used_accepted.add(idx)
    for normalized, indices in cvf_norm_groups.items():
        if normalized and len(indices) > 1:
            structural_error = True
            for idx in indices:
                row = cvf_rows[idx]
                records.append(
                    _audit_row(
                        Audit_Status=AUDIT_DUPLICATE_CVF,
                        CVF_Title=clean_cell(row.get("Title（标题）")),
                        CVF_Normalized_Title=normalized,
                        CVF_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                        Paper_ID=clean_cell(row.get("Paper_ID（论文编号）")),
                        Formal_Publication_Status=FORMAL_STATUS_UNRESOLVED,
                        Resolution="ERROR_DUPLICATE",
                        Notes="Duplicate normalized title in CVF corpus.",
                    )
                )
                used_cvf.add(idx)

    for normalized, accepted_indices in accepted_norm_groups.items():
        cvf_indices = cvf_norm_groups.get(normalized, [])
        if len(accepted_indices) != 1 or len(cvf_indices) != 1:
            continue
        ai, ci = accepted_indices[0], cvf_indices[0]
        if ai in used_accepted or ci in used_cvf:
            continue
        paper, row = accepted[ai], cvf_rows[ci]
        paper_id = clean_cell(row.get("Paper_ID（论文编号）"))
        formal_status, evidence = _cvf_formal_evidence(row, venue, year)
        accepted_presence[paper_id] = True
        original_equal = paper.title.strip() == clean_cell(row.get("Title（标题）")).strip()
        records.append(
            _audit_row(
                Audit_Status=AUDIT_EXACT,
                Accepted_Title=paper.title,
                CVF_Title=clean_cell(row.get("Title（标题）")),
                Accepted_Normalized_Title=normalized,
                CVF_Normalized_Title=normalized,
                Similarity="1.000000",
                Author_Similarity=f"{_similarity(normalize_authors(paper.authors), normalize_authors(row.get('Authors（作者）', ''))):.6f}",
                Authors=paper.authors,
                Accepted_URL=paper.official_url,
                CVF_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Formal_Publication_Status=formal_status,
                Formal_Publication_Evidence=evidence,
                Formal_Publication_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Paper_ID=paper_id,
                Resolution="INCLUDED_FORMAL_CVF" if formal_status == FORMAL_STATUS_CVF else "UNRESOLVED",
                Notes="Exact title." if original_equal else "Unique match after conservative normalization.",
            )
        )
        used_accepted.add(ai)
        used_cvf.add(ci)

    unmatched_accepted = [i for i in range(len(accepted)) if i not in used_accepted]
    unmatched_cvf = [i for i in range(len(cvf_rows)) if i not in used_cvf]
    accepted_author_groups: dict[str, list[int]] = {}
    cvf_author_groups: dict[str, list[int]] = {}
    for ai in unmatched_accepted:
        signature = normalize_authors(accepted[ai].authors)
        if signature:
            accepted_author_groups.setdefault(signature, []).append(ai)
    for ci in unmatched_cvf:
        signature = normalize_authors(cvf_rows[ci].get("Authors（作者）", ""))
        if signature:
            cvf_author_groups.setdefault(signature, []).append(ci)
    candidates: list[tuple[float, float, int, int]] = []
    pair_metrics: dict[tuple[int, int], tuple[float, float, float]] = {}
    for ai in unmatched_accepted:
        paper = accepted[ai]
        accepted_title = normalize_title(paper.title)
        for ci in unmatched_cvf:
            row = cvf_rows[ci]
            score = _similarity(accepted_title, normalize_title(row.get("Title（标题）", "")))
            author_score = _similarity(
                normalize_authors(paper.authors),
                normalize_authors(row.get("Authors（作者）", "")),
            )
            accepted_signature = normalize_authors(paper.authors)
            cvf_signature = normalize_authors(row.get("Authors（作者）", ""))
            unique_exact_authors = bool(
                accepted_signature
                and accepted_signature == cvf_signature
                and len(accepted_author_groups.get(accepted_signature, [])) == 1
                and len(cvf_author_groups.get(cvf_signature, [])) == 1
            )
            combined = 0.6 * score + 0.4 * author_score
            pair_metrics[(ai, ci)] = (score, author_score, combined)
    best_for_accepted = {
        ai: max((metrics[2], ci) for (candidate_ai, ci), metrics in pair_metrics.items() if candidate_ai == ai)[1]
        for ai in unmatched_accepted
        if any(candidate_ai == ai for candidate_ai, _ in pair_metrics)
    }
    best_for_cvf = {
        ci: max((metrics[2], ai) for (ai, candidate_ci), metrics in pair_metrics.items() if candidate_ci == ci)[1]
        for ci in unmatched_cvf
        if any(candidate_ci == ci for _, candidate_ci in pair_metrics)
    }
    for (ai, ci), (score, author_score, _) in pair_metrics.items():
        paper = accepted[ai]
        row = cvf_rows[ci]
        accepted_signature = normalize_authors(paper.authors)
        cvf_signature = normalize_authors(row.get("Authors（作者）", ""))
        unique_exact_authors = bool(
            accepted_signature
            and accepted_signature == cvf_signature
            and len(accepted_author_groups.get(accepted_signature, [])) == 1
            and len(cvf_author_groups.get(cvf_signature, [])) == 1
        )
        unique_author_lists = bool(
            accepted_signature
            and cvf_signature
            and len(accepted_author_groups.get(accepted_signature, [])) == 1
            and len(cvf_author_groups.get(cvf_signature, [])) == 1
        )
        reciprocal_corroboration = bool(
            score >= 0.45
            and author_score >= 0.85
            and unique_author_lists
            and best_for_accepted.get(ai) == ci
            and best_for_cvf.get(ci) == ai
        )
        if score >= probable_threshold or unique_exact_authors or reciprocal_corroboration:
                candidates.append((score, author_score, ai, ci))
    candidates.sort(reverse=True)
    for score, author_score, ai, ci in candidates:
        if ai in used_accepted or ci in used_cvf:
            continue
        paper, row = accepted[ai], cvf_rows[ci]
        paper_id = clean_cell(row.get("Paper_ID（论文编号）"))
        accepted_presence[paper_id] = True
        accepted_signature = normalize_authors(paper.authors)
        cvf_signature = normalize_authors(row.get("Authors（作者）", ""))
        author_identity_resolved = bool(
            accepted_signature
            and accepted_signature == cvf_signature
            and len(accepted_author_groups.get(accepted_signature, [])) == 1
            and len(cvf_author_groups.get(cvf_signature, [])) == 1
        )
        cvf_status, cvf_evidence = _cvf_formal_evidence(row, venue, year)
        reciprocal_corroboration = bool(
            score >= 0.45
            and author_score >= 0.85
            and len(accepted_author_groups.get(accepted_signature, [])) == 1
            and len(cvf_author_groups.get(cvf_signature, [])) == 1
            and best_for_accepted.get(ai) == ci
            and best_for_cvf.get(ci) == ai
        )
        corroborated_variant = bool(
            (score >= 0.90 and author_score >= 0.65) or reciprocal_corroboration
        )
        identity_resolved = author_identity_resolved or corroborated_variant
        formal_status = cvf_status if identity_resolved else FORMAL_STATUS_UNRESOLVED
        resolution = (
            "TITLE_VARIANT_RESOLVED_BY_OFFICIAL_AUTHORS"
            if identity_resolved and cvf_status == FORMAL_STATUS_CVF
            else "MANUAL_OR_OFFICIAL_VERIFICATION_REQUIRED"
        )
        records.append(
            _audit_row(
                Audit_Status=AUDIT_PROBABLE,
                Accepted_Title=paper.title,
                CVF_Title=clean_cell(row.get("Title（标题）")),
                Accepted_Normalized_Title=normalize_title(paper.title),
                CVF_Normalized_Title=normalize_title(row.get("Title（标题）", "")),
                Similarity=f"{score:.6f}",
                Author_Similarity=f"{author_score:.6f}",
                Authors=paper.authors,
                Accepted_URL=paper.official_url,
                CVF_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Formal_Publication_Status=formal_status,
                Formal_Publication_Evidence=(
                    cvf_evidence + "; unique exact normalized author identity across official sources"
                    if identity_resolved
                    else "Fuzzy title candidate only; not formal identity evidence"
                ),
                Formal_Publication_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Paper_ID=paper_id,
                Resolution=resolution,
                Notes=(
                    "Resolved by unique exact author identity across official Accepted/Program and CVF records; title similarity alone was not used."
                    if identity_resolved
                    else "Fuzzy matching never auto-confirms identity."
                ),
            )
        )
        used_accepted.add(ai)
        used_cvf.add(ci)

    for ai in range(len(accepted)):
        if ai in used_accepted:
            continue
        paper = accepted[ai]
        records.append(
            _audit_row(
                Audit_Status=AUDIT_ACCEPTED_ONLY,
                Accepted_Title=paper.title,
                Accepted_Normalized_Title=normalize_title(paper.title),
                Authors=paper.authors,
                Accepted_URL=paper.official_url,
                Formal_Publication_Status=FORMAL_STATUS_UNRESOLVED,
                Resolution="FORMAL_VERIFICATION_REQUIRED",
                Notes="Accepted/program discovery alone is insufficient for Proceedings inclusion.",
            )
        )
    for ci in range(len(cvf_rows)):
        if ci in used_cvf:
            continue
        row = cvf_rows[ci]
        formal_status, evidence = _cvf_formal_evidence(row, venue, year)
        records.append(
            _audit_row(
                Audit_Status=AUDIT_CVF_ONLY,
                CVF_Title=clean_cell(row.get("Title（标题）")),
                CVF_Normalized_Title=normalize_title(row.get("Title（标题）", "")),
                CVF_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Formal_Publication_Status=formal_status,
                Formal_Publication_Evidence=evidence,
                Formal_Publication_URL=clean_cell(row.get("Official_URL（官方论文页面）")),
                Paper_ID=clean_cell(row.get("Paper_ID（论文编号）")),
                Resolution="INCLUDED_FORMAL_CVF" if formal_status == FORMAL_STATUS_CVF else "UNRESOLVED",
                Notes="Absent from official discovery list; CVF formal evidence evaluated independently.",
            )
        )
    return records, accepted_presence, structural_error


def _attempt_ieee_verification(
    client: CachedHttpClient,
    paths: ProjectPaths,
    record: dict[str, object],
    venue: str,
    year: int,
    force: bool,
) -> dict[str, object] | None:
    title = clean_cell(record.get("Accepted_Title"))
    key = hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]
    try:
        search_url = ieee_search_url(title)
        search_html, _ = client.get_text(search_url, paths.ieee_search_cache(key), force=force)
        document_url = parse_ieee_exact_document_link(search_html, title)
        if not document_url:
            record["Notes"] = clean_cell(record.get("Notes")) + " IEEE exact public record not found or not uniquely resolvable."
            return None
        document_id = Path(urlparse(document_url).path).name
        document_html, _ = client.get_text(
            document_url, paths.ieee_document_cache(document_id), force=force
        )
        metadata = parse_ieee_document_metadata(document_html)
        conference = metadata["conference"].casefold()
        date = metadata["date"]
        if (
            normalize_title(metadata["title"]) != normalize_title(title)
            or ("computer vision and pattern recognition" not in conference and "cvpr" not in conference)
            or str(year) not in date
        ):
            record["Notes"] = clean_cell(record.get("Notes")) + " IEEE metadata did not confirm exact venue/year/title."
            return None
        identity = metadata["doi"] or document_url or normalize_title(title)
        paper_id = stable_supplemental_paper_id(venue, year, "IEEE", identity)
        record.update(
            {
                "Formal_Publication_Status": FORMAL_STATUS_IEEE,
                "Formal_Publication_Evidence": "IEEE Xplore document citation metadata confirms exact title, CVPR, and year",
                "Formal_Publication_URL": document_url,
                "Paper_ID": paper_id,
                "Resolution": "INCLUDED_FORMAL_IEEE_SUPPLEMENT",
            }
        )
        return {
            "Paper_ID（论文编号）": paper_id,
            "Title（标题）": metadata["title"] or title,
            "Authors（作者）": metadata["authors"] or clean_cell(record.get("Authors")),
            "Abstract（摘要）": metadata["abstract"],
            "Venue（会议/期刊）": venue.upper(),
            "Year（年份）": str(year),
            "Track（论文轨道）": "Main Conference",
            "Official_URL（官方论文页面）": document_url,
            "PDF_URL（官方PDF链接）": metadata["pdf_url"],
            "BibTeX（BibTeX信息）": "",
            "Crawl_Status（抓取状态）": "SUPPLEMENTAL_OFFICIAL",
            "Crawl_Error（抓取错误）": "",
            "Notes（备注）": "Official IEEE supplemental record; not present in original CVF crawl.",
            "Corpus_Source": "IEEE_XPLORE_SUPPLEMENT",
            "Formal_Publication_Status": FORMAL_STATUS_IEEE,
            "Formal_Publication_Evidence": clean_cell(record.get("Formal_Publication_Evidence")),
            "Formal_Publication_URL": document_url,
            "Original_CVF_Present": "FALSE",
            "Official_Accepted_Present": "TRUE",
            "Corpus_Audit_Notes": "Added only after official IEEE formal-publication confirmation.",
        }
    except Exception as exc:  # public access may legitimately be blocked/unavailable
        record["Notes"] = clean_cell(record.get("Notes")) + f" IEEE verification unavailable: {type(exc).__name__}: {exc}"
        return None


def build_formal_corpus(
    cvf: pd.DataFrame,
    accepted_presence: dict[str, bool],
    supplemental_rows: Iterable[dict[str, object]],
    venue: str,
    year: int,
) -> pd.DataFrame:
    """Build the formal corpus from confirmed CVF and official supplemental rows."""
    rows: list[dict[str, object]] = []
    for raw_row in cvf.to_dict(orient="records"):
        status, evidence = _cvf_formal_evidence(raw_row, venue, year)
        if status != FORMAL_STATUS_CVF:
            continue
        row = {column: clean_cell(raw_row.get(column)) for column in RAW_COLUMNS}
        paper_id = row["Paper_ID（论文编号）"]
        row.update(
            {
                "Corpus_Source": "CVF_OPEN_ACCESS",
                "Formal_Publication_Status": status,
                "Formal_Publication_Evidence": evidence,
                "Formal_Publication_URL": row["Official_URL（官方论文页面）"],
                "Original_CVF_Present": "TRUE",
                "Official_Accepted_Present": "TRUE" if accepted_presence.get(paper_id) else "FALSE",
                "Corpus_Audit_Notes": "Original CVF crawl record retained without source rewriting.",
            }
        )
        rows.append(row)
    for supplemental in supplemental_rows:
        if clean_cell(supplemental.get("Formal_Publication_Status")) == FORMAL_STATUS_IEEE:
            rows.append({column: clean_cell(supplemental.get(column)) for column in FORMAL_CORPUS_COLUMNS})
    return pd.DataFrame(rows).reindex(columns=FORMAL_CORPUS_COLUMNS, fill_value="")


def _metadata_incomplete(formal: pd.DataFrame) -> pd.DataFrame:
    if formal.empty:
        return formal.copy()
    mask = formal[REQUIRED_FORMAL_METADATA].apply(
        lambda row: any(not clean_cell(value).strip() for value in row), axis=1
    )
    return formal.loc[mask].copy()


def _read_exception_count(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    return len(frame)


def _write_compact_report(
    paths: ProjectPaths, venue: str, year: int, counts: dict[str, int], status: str
) -> None:
    lines = [f"# {venue.upper()} {year} Formal Proceedings Corpus Audit", ""]
    if status != COMPLETENESS_VERIFIED:
        lines = ["# WARNING", "", f"{venue.upper()} {year} Formal Proceedings Corpus Audit", ""]
    lines.extend(
        [
            f"- CVF Corpus: {counts['cvf_corpus']}",
            f"- Official Accepted/Program List: {counts['official_accepted']}",
            f"- Exact/Normalized Matches: {counts['exact_or_normalized']}",
            f"- Probable Title Variants: {counts['probable_variant']}",
            f"- Accepted Only: {counts['accepted_only']}",
            f"- CVF Only: {counts['cvf_only']}",
            f"- Formal CVF Confirmed: {counts['formal_cvf_confirmed']}",
            f"- Formal IEEE Supplemental: {counts['formal_ieee_supplemental']}",
            f"- Not Formally Published: {counts['not_formally_published']}",
            f"- Non-target Official Track: {counts['non_target_official_track']}",
            f"- Unresolved: {counts['unresolved']}",
            f"- Final Formal Proceedings Corpus: {counts['final_formal_corpus']}",
            f"- Crawl Exceptions: {counts['crawl_exceptions']}",
            f"- Metadata Incomplete: {counts['metadata_incomplete']}",
            f"- Duplicate Titles: {counts['duplicate_titles']}",
            "",
            f"Status: {status}",
        ]
    )
    if status != COMPLETENESS_VERIFIED:
        lines.extend(["", "Action required: resolve audit exceptions, then rerun audit-corpus."])
    paths.corpus_completeness_report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_corpus(
    root: Path,
    venue: str,
    year: int,
    source_config_path: Path,
    *,
    force: bool = False,
) -> CorpusAuditResult:
    """Cross-check raw CVF provenance with official discovery sources."""
    venue = venue.upper()
    paths = ProjectPaths(root, venue, year)
    raw_source = paths.source_raw_csv()
    if not raw_source.exists():
        raise FileNotFoundError(f"CVF raw provenance file not found: {raw_source}")
    paths.ensure_stage("audit-corpus")
    logger = setup_logger(f"corpus_audit.{venue}.{year}", paths.corpus_audit_log)
    config = load_yaml(source_config_path)
    client = CachedHttpClient(config.get("http", {}), logger)
    cvf = pd.read_csv(raw_source, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing_raw_columns = [column for column in RAW_COLUMNS if column not in cvf.columns]
    if missing_raw_columns:
        raise ValueError(f"Raw corpus missing required columns: {missing_raw_columns}")

    official, discovery_source = fetch_official_discovery_papers(
        client, paths, venue, year, force=force
    )
    official_rows = [
        {
            "Official_Title": paper.title,
            "Normalized_Title": normalize_title(paper.title),
            "Authors": paper.authors,
            "Venue": venue,
            "Year": year,
            "Official_Accepted_URL": paper.official_url,
            "Discovery_Source": paper.discovery_source,
        }
        for paper in official
    ]
    write_csv(official_rows, paths.official_accepted_csv, OFFICIAL_ACCEPTED_COLUMNS)

    records, accepted_presence, structural_error = _match_records(cvf, official, venue, year)
    supplemental_rows: list[dict[str, object]] = []
    accepted_only_records = [
        record for record in records if record["Audit_Status"] == AUDIT_ACCEPTED_ONLY
    ]
    if accepted_only_records:
        findings_entries = fetch_cvf_findings_entries(
            client, paths, venue, year, force=force
        )
        findings_by_title: dict[str, list[object]] = {}
        for entry in findings_entries:
            findings_by_title.setdefault(normalize_title(entry.title), []).append(entry)
        for record in accepted_only_records:
            matches = findings_by_title.get(
                normalize_title(record.get("Accepted_Title", "")), []
            )
            if len(matches) == 1:
                entry = matches[0]
                record.update(
                    {
                        "Formal_Publication_Status": FORMAL_STATUS_NON_TARGET,
                        "Formal_Publication_Evidence": "CVF Open Access Findings listing confirms a formal non-Main track record",
                        "Formal_Publication_URL": entry.official_url,
                        "Resolution": "EXCLUDED_NON_TARGET_FINDINGS",
                        "Notes": "Official Findings paper; deliberately excluded from the Main Conference corpus.",
                    }
                )
    for record in records:
        if (
            record["Audit_Status"] == AUDIT_ACCEPTED_ONLY
            and record["Formal_Publication_Status"] == FORMAL_STATUS_UNRESOLVED
        ):
            supplemental = _attempt_ieee_verification(
                client, paths, record, venue, year, force
            )
            if supplemental is not None:
                supplemental_rows.append(supplemental)

    audit = pd.DataFrame(records).reindex(columns=AUDIT_COLUMNS, fill_value="")
    write_csv(audit, paths.corpus_completeness_audit_csv, AUDIT_COLUMNS)
    formal = build_formal_corpus(cvf, accepted_presence, supplemental_rows, venue, year)
    write_csv(formal, paths.formal_corpus_csv, FORMAL_CORPUS_COLUMNS)
    write_xlsx(formal, paths.formal_corpus_xlsx, FORMAL_CORPUS_COLUMNS)
    incomplete = _metadata_incomplete(formal)
    write_csv(incomplete, paths.formal_metadata_incomplete_csv, FORMAL_CORPUS_COLUMNS)

    if supplemental_rows:
        write_csv(
            pd.DataFrame(supplemental_rows),
            paths.formal_proceedings_supplement_csv,
            FORMAL_CORPUS_COLUMNS,
        )

    crawl_exceptions = _read_exception_count(paths.source_crawl_exception_csv())
    count_status = audit["Audit_Status"].value_counts().to_dict()
    count_formal = audit["Formal_Publication_Status"].value_counts().to_dict()
    counts = {
        "cvf_corpus": len(cvf),
        "official_accepted": len(official),
        "exact_or_normalized": int(count_status.get(AUDIT_EXACT, 0)),
        "probable_variant": int(count_status.get(AUDIT_PROBABLE, 0)),
        "accepted_only": int(count_status.get(AUDIT_ACCEPTED_ONLY, 0)),
        "cvf_only": int(count_status.get(AUDIT_CVF_ONLY, 0)),
        "formal_cvf_confirmed": int((formal["Formal_Publication_Status"] == FORMAL_STATUS_CVF).sum()),
        "formal_ieee_supplemental": int((formal["Formal_Publication_Status"] == FORMAL_STATUS_IEEE).sum()),
        "not_formally_published": int(count_formal.get(FORMAL_STATUS_NOT, 0)),
        "non_target_official_track": int(count_formal.get(FORMAL_STATUS_NON_TARGET, 0)),
        "unresolved": int(count_formal.get(FORMAL_STATUS_UNRESOLVED, 0)),
        "final_formal_corpus": len(formal),
        "crawl_exceptions": crawl_exceptions,
        "metadata_incomplete": len(incomplete),
        "duplicate_titles": int(
            count_status.get(AUDIT_DUPLICATE_ACCEPTED, 0)
            + count_status.get(AUDIT_DUPLICATE_CVF, 0)
        ),
    }
    if structural_error:
        status = COMPLETENESS_ERROR
    elif any(
        (
            counts["crawl_exceptions"],
            counts["unresolved"],
            counts["metadata_incomplete"],
        )
    ):
        status = COMPLETENESS_REVIEW
    else:
        status = COMPLETENESS_VERIFIED

    _write_compact_report(paths, venue, year, counts, status)
    status_payload = {
        "venue": venue,
        "year": year,
        "status": status,
        "discovery_source": discovery_source,
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_source_path": str(raw_source),
        "raw_source_sha256": file_sha256(raw_source),
        "formal_corpus_path": str(paths.formal_corpus_csv),
        "formal_corpus_sha256": file_sha256(paths.formal_corpus_csv),
        "counts": counts,
    }
    paths.corpus_audit_status_json.write_text(
        json.dumps(status_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("Corpus audit complete: status=%s counts=%s", status, counts)
    return CorpusAuditResult(status, counts, audit, formal, paths.corpus_completeness_report)


def require_verified_formal_corpus(paths: ProjectPaths) -> Path:
    """Validate the audit gate and return the verified formal corpus path."""
    if not paths.formal_corpus_csv.exists() or not paths.corpus_audit_status_json.exists():
        raise FileNotFoundError("Corpus completeness audit is required before screening.")
    try:
        payload = json.loads(paths.corpus_audit_status_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("Formal proceedings corpus audit status is unreadable.") from exc
    if payload.get("status") != COMPLETENESS_VERIFIED:
        raise ValueError("Formal proceedings corpus is not verified.")
    expected_hash = clean_cell(payload.get("formal_corpus_sha256"))
    if not expected_hash or file_sha256(paths.formal_corpus_csv) != expected_hash:
        raise ValueError("Formal proceedings corpus changed after audit; rerun audit-corpus.")
    return paths.formal_corpus_csv
