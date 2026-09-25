"""Parsers for CVF conference listings and paper detail pages."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ListingEntry:
    title: str
    official_url: str


@dataclass(frozen=True)
class WorkshopEntry:
    name: str
    listing_url: str


def parse_cvf_listing(html: str, base_url: str, expected_path_marker: str) -> list[ListingEntry]:
    """Parse CVF `dt.ptitle` entries and reject links from a different track tree."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ListingEntry] = []
    seen: set[str] = set()
    for title_node in soup.select("dt.ptitle"):
        anchor = title_node.find("a", href=True)
        if not anchor:
            continue
        official_url = urljoin(base_url, anchor["href"])
        if expected_path_marker not in urlparse(official_url).path:
            continue
        if official_url in seen:
            continue
        title = anchor.get_text(" ", strip=True)
        if title:
            entries.append(ListingEntry(title=title, official_url=official_url))
            seen.add(official_url)
    return entries


def parse_cvf_workshop_index(html: str, base_url: str, path_prefix: str) -> list[WorkshopEntry]:
    """Parse the CVF workshop hub into named workshop listing URLs."""
    soup = BeautifulSoup(html, "html.parser")
    workshops: list[WorkshopEntry] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        listing_url = urljoin(base_url, anchor["href"])
        path = urlparse(listing_url).path
        if not path.startswith(path_prefix) or path.rstrip("/") == path_prefix.rstrip("/"):
            continue
        if listing_url in seen:
            continue
        name = anchor.get_text(" ", strip=True)
        if name:
            workshops.append(WorkshopEntry(name=name, listing_url=listing_url))
            seen.add(listing_url)
    return workshops


def _meta_values(soup: BeautifulSoup, name: str) -> list[str]:
    values: list[str] = []
    for node in soup.select(f'meta[name="{name}"]'):
        content = node.get("content", "").strip()
        if content:
            values.append(content)
    return values


def parse_cvf_detail(html: str, official_url: str) -> dict[str, str]:
    """Extract stable CVF metadata, abstract, PDF link, and BibTeX."""
    soup = BeautifulSoup(html, "html.parser")
    title_values = _meta_values(soup, "citation_title")
    authors = _meta_values(soup, "citation_author")
    pdf_values = _meta_values(soup, "citation_pdf_url")

    if title_values:
        title = title_values[0]
    else:
        title_node = soup.select_one("#papertitle") or soup.find("h1")
        title = title_node.get_text(" ", strip=True) if title_node else ""

    abstract_node = soup.select_one("div#abstract")
    abstract = abstract_node.get_text(" ", strip=True) if abstract_node else ""
    bib_node = soup.select_one("div.bibref")
    bibtex = bib_node.get_text("\n", strip=True) if bib_node else ""

    if pdf_values:
        pdf_url = urljoin(official_url, pdf_values[0])
    else:
        pdf_anchor = soup.select_one('a[href$="_paper.pdf"]')
        pdf_url = urljoin(official_url, pdf_anchor["href"]) if pdf_anchor else ""

    return {
        "title": title,
        "authors": "; ".join(authors),
        "abstract": abstract,
        "pdf_url": pdf_url,
        "bibtex": bibtex,
    }
