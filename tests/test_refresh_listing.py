from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from main import build_parser
from src.crawler import crawl_cvf
from src.paths import ProjectPaths
from src.utils import RAW_COLUMNS, stable_paper_id, write_csv


VENUE = "CVPR"
YEAR = 2026
BASE = "https://openaccess.thecvf.com"
LISTING_URL = f"{BASE}/CVPR{YEAR}?day=all"
DAY_INDEX_URL = f"{BASE}/CVPR{YEAR}"
DAY_1 = "2026-06-05"
DAY_2 = "2026-06-06"
DAY_1_URL = f"{BASE}/CVPR{YEAR}?day={DAY_1}"
DAY_2_URL = f"{BASE}/CVPR{YEAR}?day={DAY_2}"


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


def _listing(*slugs: str, daily_dates: tuple[str, ...] = ()) -> str:
    day_links = "".join(
        f'<a href="/CVPR2026?day={day}">{day}</a>' for day in daily_dates
    )
    links = "".join(
        f'<dt class="ptitle"><a href="/content/CVPR2026/html/{slug}_CVPR_2026_paper.html">'
        f"{slug} Paper</a></dt>"
        for slug in slugs
    )
    return f"<html><body>{day_links}<dl>{links}</dl></body></html>"


def _detail(slug: str) -> str:
    return f"""
    <html><head>
      <meta name="citation_title" content="{slug} Paper">
      <meta name="citation_author" content="Example, Author">
      <meta name="citation_pdf_url" content="{BASE}/content/CVPR2026/papers/{slug}.pdf">
    </head><body>
      <div id="abstract">Abstract for {slug}.</div>
      <div class="bibref pre-white-space">@InProceedings{{{slug}_2026_CVPR}}</div>
    </body></html>
    """


def _url(slug: str) -> str:
    return f"{BASE}/content/CVPR2026/html/{slug}_CVPR_2026_paper.html"


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "source.yaml"
    path.write_text(
        """
http:
  user_agent: PaperSearchTool-Test
  timeout_seconds: 1
  retries: 0
  backoff_factor: 0
  request_interval_seconds: 0
venues:
  CVPR:
    base_url: https://openaccess.thecvf.com
    tracks:
      Main Conference:
        enabled: true
        listing_url: /CVPR{year}?day=all
        detail_path_marker: /content/CVPR{year}/html/
      Findings:
        enabled: false
        listing_url: /CVPR{year}_findings?day=all
        detail_path_marker: /content/CVPR{year}F/html/
      Workshop:
        enabled: false
        listing_type: workshop_index
        listing_url: /CVPR{year}_workshops
        workshop_path_prefix: /CVPR{year}_workshops/
        detail_path_marker: /content/CVPR{year}W/
""".strip(),
        encoding="utf-8",
    )
    return path


def _success_row(slug: str) -> dict[str, object]:
    official_url = _url(slug)
    return {
        "Paper_ID（论文编号）": stable_paper_id(VENUE, YEAR, "Main Conference", official_url),
        "Title（标题）": f"{slug} Paper",
        "Authors（作者）": "Example, Author",
        "Abstract（摘要）": f"Abstract for {slug}.",
        "Venue（会议/期刊）": VENUE,
        "Year（年份）": YEAR,
        "Track（论文轨道）": "Main Conference",
        "Official_URL（官方论文页面）": official_url,
        "PDF_URL（官方PDF链接）": f"{BASE}/content/CVPR2026/papers/{slug}.pdf",
        "BibTeX（BibTeX信息）": f"@InProceedings{{{slug}_2026_CVPR}}",
        "Crawl_Status（抓取状态）": "SUCCESS",
        "Crawl_Error（抓取错误）": "",
        "Notes（备注）": "fixture",
    }


def _failed_row(slug: str) -> dict[str, object]:
    row = _success_row(slug)
    row["Authors（作者）"] = ""
    row["Abstract（摘要）"] = ""
    row["PDF_URL（官方PDF链接）"] = ""
    row["BibTeX（BibTeX信息）"] = ""
    row["Crawl_Status（抓取状态）"] = "FAILED"
    row["Crawl_Error（抓取错误）"] = "fixture failure"
    return row


def _seed_legacy_raw(paths: ProjectPaths, *slugs: str) -> None:
    write_csv([_success_row(slug) for slug in slugs], paths.legacy_raw_csv, RAW_COLUMNS)


def _seed_listing_cache(paths: ProjectPaths, *slugs: str) -> None:
    cache = paths.listing_cache("CVPR2026_Main_Conference_listing.html")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_listing(*slugs, daily_dates=(DAY_1,)), encoding="utf-8")
    paths.listing_cache("CVPR2026_Main_Conference_day_2026-06-05.html").write_text(
        _listing(*slugs), encoding="utf-8"
    )


def _install_fake_network(monkeypatch, responses: dict[str, str | Exception]) -> list[str]:
    calls: list[str] = []

    def fake_get(_session, url: str, timeout: float):
        calls.append(url)
        if url not in responses:
            raise AssertionError(f"Unexpected network request: {url}")
        response = responses[url]
        if isinstance(response, Exception):
            raise response
        return _FakeResponse(response)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return calls


def test_normal_crawl_uses_cached_listing_and_legacy_success_row(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    _seed_listing_cache(paths, "Old")
    _seed_legacy_raw(paths, "Old")
    calls = _install_fake_network(monkeypatch, {})

    frame = crawl_cvf(tmp_path, VENUE, YEAR, _config(tmp_path))

    assert calls == []
    assert frame["Title（标题）"].tolist() == ["Old Paper"]


def test_refresh_listing_refetches_listing_but_only_fetches_new_detail(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    _seed_listing_cache(paths, "Old")
    _seed_legacy_raw(paths, "Old")
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("Old", "New", daily_dates=(DAY_1,)),
            DAY_1_URL: _listing("Old", "New"),
            _url("New"): _detail("New"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert calls == [LISTING_URL, DAY_1_URL, _url("New")]
    assert set(frame["Title（标题）"]) == {"Old Paper", "New Paper"}
    assert paths.raw_csv.exists()
    new_entries = pd.read_csv(paths.cvf_listing_new_entries_csv, encoding="utf-8-sig")
    assert new_entries["Title（标题）"].tolist() == ["New Paper"]
    assert new_entries["Crawl_Status（抓取状态）"].tolist() == ["SUCCESS"]


def test_refresh_listing_keeps_success_row_missing_from_new_listing(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    _seed_legacy_raw(paths, "Old")
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("New", daily_dates=(DAY_1,)),
            DAY_1_URL: _listing("New"),
            _url("New"): _detail("New"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert calls == [LISTING_URL, DAY_1_URL, _url("New")]
    assert set(frame["Title（标题）"]) == {"Old Paper", "New Paper"}
    exceptions = pd.read_csv(paths.crawl_exception_csv, encoding="utf-8-sig")
    assert "Listing_Removed_or_Missing" in exceptions["Crawl_Error（抓取错误）"].tolist()


def test_force_refetches_listing_and_existing_success_detail(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    _seed_listing_cache(paths, "Stale")
    _seed_legacy_raw(paths, "Old")
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("Old", daily_dates=(DAY_1,)),
            DAY_1_URL: _listing("Old"),
            _url("Old"): _detail("Old"),
        },
    )

    crawl_cvf(tmp_path, VENUE, YEAR, _config(tmp_path), force=True)

    assert calls == [LISTING_URL, DAY_1_URL, _url("Old")]
    assert not paths.cvf_listing_new_entries_csv.exists()


def test_refresh_listing_retries_existing_failed_detail(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    write_csv([_failed_row("Retry")], paths.legacy_raw_csv, RAW_COLUMNS)
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("Retry", daily_dates=(DAY_1,)),
            DAY_1_URL: _listing("Retry"),
            _url("Retry"): _detail("Retry"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert calls == [LISTING_URL, DAY_1_URL, _url("Retry")]
    assert frame.loc[0, "Crawl_Status（抓取状态）"] == "SUCCESS"
    assert pd.read_csv(paths.cvf_listing_new_entries_csv, encoding="utf-8-sig").empty


def test_refresh_listing_cli_is_available_only_for_crawl_and_all():
    parser = build_parser()
    crawl_args = parser.parse_args(["crawl", "--venue", "CVPR", "--year", "2026", "--refresh-listing"])
    all_args = parser.parse_args(["all", "--venue", "CVPR", "--year", "2026", "--refresh-listing"])

    assert crawl_args.refresh_listing is True
    assert all_args.refresh_listing is True
    assert not hasattr(parser.parse_args(["screen", "--venue", "CVPR", "--year", "2026"]), "refresh_listing")


def test_day_index_and_daily_page_extend_day_all_union(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("One", "Two"),
            DAY_INDEX_URL: _listing(daily_dates=(DAY_1,)),
            DAY_1_URL: _listing("One", "Two", "Three"),
            _url("One"): _detail("One"),
            _url("Two"): _detail("Two"),
            _url("Three"): _detail("Three"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert calls == [
        LISTING_URL,
        DAY_INDEX_URL,
        DAY_1_URL,
        _url("One"),
        _url("Two"),
        _url("Three"),
    ]
    assert set(frame["Title（标题）"]) == {"One Paper", "Two Paper", "Three Paper"}
    assert len(frame) == 3
    assert len(pd.read_csv(paths.cvf_listing_new_entries_csv, encoding="utf-8-sig")) == 3


def test_duplicate_urls_across_multiple_daily_pages_are_fetched_once(tmp_path, monkeypatch):
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("One", daily_dates=(DAY_1, DAY_2)),
            DAY_1_URL: _listing("One", "Two"),
            DAY_2_URL: _listing("One", "Two", "Three"),
            _url("One"): _detail("One"),
            _url("Two"): _detail("Two"),
            _url("Three"): _detail("Three"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert len(frame) == 3
    assert calls.count(_url("One")) == 1
    assert calls.count(_url("Two")) == 1
    assert calls.count(_url("Three")) == 1


def test_daily_listing_failure_is_recorded_without_deleting_old_success(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    _seed_legacy_raw(paths, "Old")
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: _listing("Old", daily_dates=(DAY_1,)),
            DAY_1_URL: requests.ConnectionError("daily unavailable"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert calls == [LISTING_URL, DAY_1_URL]
    assert frame["Title（标题）"].tolist() == ["Old Paper"]
    exceptions = pd.read_csv(paths.crawl_exception_csv, encoding="utf-8-sig")
    assert exceptions["Track（论文轨道）"].str.startswith("Main Conference Daily").any()
    assert exceptions["Crawl_Error（抓取错误）"].str.contains("daily unavailable").any()


def test_duplicate_title_with_different_urls_is_kept_and_reported(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path, VENUE, YEAR)
    duplicate_title_listing = f"""
    <a href="/CVPR2026?day={DAY_1}">{DAY_1}</a>
    <dt class="ptitle"><a href="/content/CVPR2026/html/One_CVPR_2026_paper.html">Same Title</a></dt>
    <dt class="ptitle"><a href="/content/CVPR2026/html/Two_CVPR_2026_paper.html">Same Title</a></dt>
    """
    calls = _install_fake_network(
        monkeypatch,
        {
            LISTING_URL: duplicate_title_listing,
            DAY_1_URL: duplicate_title_listing,
            _url("One"): _detail("One"),
            _url("Two"): _detail("Two"),
        },
    )

    frame = crawl_cvf(
        tmp_path,
        VENUE,
        YEAR,
        _config(tmp_path),
        refresh_listing=True,
    )

    assert len(frame) == 2
    assert calls.count(_url("One")) == 1
    assert calls.count(_url("Two")) == 1
    exceptions = pd.read_csv(paths.crawl_exception_csv, encoding="utf-8-sig")
    assert exceptions["Crawl_Error（抓取错误）"].str.startswith(
        "Duplicate_Title_Different_Official_URL"
    ).any()
