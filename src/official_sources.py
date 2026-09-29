"""Official discovery and formal-publication source parsers for corpus audits.

Accepted/program pages are discovery sources only.  They never establish formal
Proceedings membership by themselves.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .crawler import CachedHttpClient
from .parser import ListingEntry, parse_cvf_listing
from .paths import ProjectPaths


OFFICIAL_ACCEPTED_COLUMNS = [
    "Official_Title",
    "Normalized_Title",
    "Authors",
    "Venue",
    "Year",
    "Official_Accepted_URL",
    "Discovery_Source",
]

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {
    ord("‘"): "'",
    ord("’"): "'",
    ord("‚"): "'",
    ord("‛"): "'",
    ord("“"): '"',
    ord("”"): '"',
    ord("„"): '"',
    ord("‟"): '"',
}


@dataclass(frozen=True)
class OfficialPaper:
    title: str
    authors: str
    official_url: str
    discovery_source: str


def normalize_title(value: object) -> str:
    """Conservatively normalize a title without discarding semantic symbols."""
    text = html_lib.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DASHES).translate(_QUOTES)
    text = re.sub(r"\s+", " ", text).strip()
    # Only trim punctuation that cannot distinguish a title at its outer edge.
    text = text.strip(" \t\r\n.,;:!?\"'")
    return text.casefold()


def normalize_authors(value: object) -> str:
    """Canonicalize an author list while tolerating ``Family, Given`` ordering."""
    text = html_lib.unescape(str(value or ""))
    text = unicodedata.normalize("NFKD", text).translate(_QUOTES)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("⋅", ";")
    identities: list[str] = []
    for author in re.split(r"\s*;\s*", text):
        parts = author.split(",", 1)
        ordered = f"{parts[1]} {parts[0]}" if len(parts) == 2 else author
        tokens = re.findall(r"[^\W_]+", ordered.casefold(), flags=re.UNICODE)
        # Accepted/program profiles sometimes omit middle initials; ignoring only
        # one-character tokens keeps this comparison conservative but practical.
        tokens = [token for token in tokens if len(token) > 1]
        if tokens:
            # Character sorting tolerates profile display order (e.g. East-Asian
            # family/given order) and hyphen segmentation without merging authors.
            identities.append("".join(sorted("".join(tokens))))
    return ";".join(sorted(identities))


def parse_official_accepted_papers(
    page_html: str, official_url: str
) -> list[OfficialPaper]:
    """Parse the public CVPR Accepted Papers table."""
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.select_one("table.elc-table")
    if table is None:
        return []
    papers: list[OfficialPaper] = []
    for row in table.select("tr"):
        title_node = row.select_one("td strong")
        if title_node is None:
            continue
        title = title_node.get_text(" ", strip=True)
        author_node = row.select_one("td div.indented i")
        authors = author_node.get_text(" ", strip=True) if author_node else ""
        if title:
            papers.append(
                OfficialPaper(title, authors, official_url, "OFFICIAL_ACCEPTED_PAGE")
            )
    return papers


def parse_main_program_session_links(
    calendar_html: str, calendar_url: str, year: int
) -> list[str]:
    """Return official Main Conference paper-session URLs, excluding Findings."""
    soup = BeautifulSoup(calendar_html, "html.parser")
    marker = f"/virtual/{year}/session/"
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select(f'a[href*="{marker}"]'):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        folded = label.casefold()
        if not ("poster session" in folded or "oral session" in folded) or "findings" in folded:
            continue
        url = urljoin(calendar_url, str(anchor.get("href", "")))
        if url and url not in seen:
            links.append(url)
            seen.add(url)
    return links


def parse_main_program_session(
    session_html: str, session_url: str, year: int
) -> list[OfficialPaper]:
    """Parse paper title/authors from one official CVPR poster session."""
    soup = BeautifulSoup(session_html, "html.parser")
    papers: list[OfficialPaper] = []
    seen_urls: set[str] = set()
    selectors = (
        f'h5 strong a[href*="/virtual/{year}/poster/"], '
        f'h5 strong a[href*="/virtual/{year}/oral/"]'
    )
    for anchor in soup.select(selectors):
        paper_url = urljoin(session_url, str(anchor.get("href", "")))
        if paper_url in seen_urls:
            continue
        card = anchor.find_parent(class_="track-schedule-card")
        author_node = card.select_one("p.text-muted") if card else None
        title = anchor.get_text(" ", strip=True)
        authors = author_node.get_text(" ", strip=True) if author_node else ""
        if title:
            papers.append(
                OfficialPaper(title, authors, paper_url, "OFFICIAL_MAIN_PROGRAM")
            )
            seen_urls.add(paper_url)
    return papers


def _require_complete_html(page_html: str, url: str) -> None:
    if "</html>" not in page_html.casefold():
        raise ValueError(f"Official HTML response is incomplete/truncated: {url}")


def fetch_official_discovery_papers(
    client: CachedHttpClient,
    paths: ProjectPaths,
    venue: str,
    year: int,
    *,
    force: bool = False,
) -> tuple[list[OfficialPaper], str]:
    """Fetch Accepted Papers, falling back to the official Main program on 404.

    The fallback is needed for years such as CVPR 2026 where the public
    ``AcceptedPapers`` route is absent but the official program is available.
    """
    if venue.upper() != "CVPR":
        raise ValueError("audit-corpus official discovery adapter currently supports CVPR only")
    accepted_url = f"https://cvpr.thecvf.com/Conferences/{year}/AcceptedPapers"
    unavailable_marker = paths.accepted_page_unavailable_marker()
    if force or not unavailable_marker.exists():
        try:
            page_html, _ = client.get_text(
                accepted_url, paths.accepted_page_cache(), force=force
            )
            _require_complete_html(page_html, accepted_url)
            papers = parse_official_accepted_papers(page_html, accepted_url)
            if not papers:
                raise ValueError(f"No papers parsed from official Accepted page: {accepted_url}")
            if unavailable_marker.exists():
                unavailable_marker.unlink()
            return papers, "OFFICIAL_ACCEPTED_PAGE"
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            unavailable_marker.parent.mkdir(parents=True, exist_ok=True)
            unavailable_marker.write_text(
                f"404 NOT FOUND: {accepted_url}\n", encoding="utf-8"
            )

    calendar_url = f"https://cvpr.thecvf.com/virtual/{year}/calendar"
    calendar_html, _ = client.get_text(
        calendar_url, paths.program_calendar_cache(), force=force
    )
    _require_complete_html(calendar_html, calendar_url)
    session_urls = parse_main_program_session_links(calendar_html, calendar_url, year)
    if not session_urls:
        raise ValueError(f"No Main Conference poster sessions found: {calendar_url}")

    papers: list[OfficialPaper] = []
    for session_url in session_urls:
        session_id = Path(urlparse(session_url).path).name
        session_html, _ = client.get_text(
            session_url, paths.program_session_cache(session_id), force=force
        )
        _require_complete_html(session_html, session_url)
        parsed = parse_main_program_session(session_html, session_url, year)
        if not parsed:
            raise ValueError(f"No papers parsed from official program session: {session_url}")
        papers.extend(parsed)
    deduplicated: list[OfficialPaper] = []
    seen: set[tuple[str, str]] = set()
    for paper in papers:
        key = (normalize_title(paper.title), normalize_authors(paper.authors))
        if key not in seen:
            deduplicated.append(paper)
            seen.add(key)
    return deduplicated, "OFFICIAL_MAIN_PROGRAM"


def fetch_cvf_findings_entries(
    client: CachedHttpClient,
    paths: ProjectPaths,
    venue: str,
    year: int,
    *,
    force: bool = False,
) -> list[ListingEntry]:
    """Read the official CVF Findings list only to exclude non-target-track records."""
    if venue.upper() != "CVPR":
        return []
    url = f"https://openaccess.thecvf.com/CVPR{year}_findings?day=all"
    try:
        page_html, _ = client.get_text(url, paths.findings_listing_cache(), force=force)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return []
        raise
    _require_complete_html(page_html, url)
    return parse_cvf_listing(
        page_html,
        "https://openaccess.thecvf.com",
        f"/content/CVPR{year}F/html/",
    )


def ieee_search_url(title: str) -> str:
    return (
        "https://ieeexplore.ieee.org/search/searchresult.jsp?newsearch=true&queryText="
        + quote_plus(f'"{title}"')
    )


def parse_ieee_exact_document_link(search_html: str, title: str) -> str:
    """Return a unique IEEE document link only when an exact title anchor exists."""
    expected = normalize_title(title)
    soup = BeautifulSoup(search_html, "html.parser")
    links: set[str] = set()
    for anchor in soup.select('a[href*="/document/"]'):
        if normalize_title(anchor.get_text(" ", strip=True)) == expected:
            match = re.search(r"/document/(\d+)", str(anchor.get("href", "")))
            if match:
                links.add(f"https://ieeexplore.ieee.org/document/{match.group(1)}")
    return next(iter(links)) if len(links) == 1 else ""


def parse_ieee_document_metadata(document_html: str) -> dict[str, str]:
    """Extract public citation metadata from an IEEE document page."""
    soup = BeautifulSoup(document_html, "html.parser")

    def first(name: str) -> str:
        node = soup.select_one(f'meta[name="{name}"]')
        return str(node.get("content", "")).strip() if node else ""

    authors = [
        str(node.get("content", "")).strip()
        for node in soup.select('meta[name="citation_author"]')
        if str(node.get("content", "")).strip()
    ]
    return {
        "title": first("citation_title"),
        "authors": "; ".join(authors),
        "conference": first("citation_conference_title") or first("citation_journal_title"),
        "date": first("citation_publication_date") or first("citation_date"),
        "pdf_url": first("citation_pdf_url"),
        "doi": first("citation_doi"),
        "abstract": first("description"),
    }
