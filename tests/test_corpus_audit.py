import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import main as main_module
import src.corpus_audit as audit_module
from src.corpus_audit import (
    AUDIT_ACCEPTED_ONLY,
    AUDIT_CVF_ONLY,
    AUDIT_EXACT,
    AUDIT_PROBABLE,
    COMPLETENESS_REVIEW,
    COMPLETENESS_VERIFIED,
    FORMAL_CORPUS_COLUMNS,
    FORMAL_STATUS_CVF,
    FORMAL_STATUS_IEEE,
    FORMAL_STATUS_NOT,
    _match_records,
    audit_corpus,
    build_formal_corpus,
    file_sha256,
    stable_supplemental_paper_id,
)
from src.official_sources import (
    OfficialPaper,
    fetch_official_discovery_papers,
    normalize_authors,
    normalize_title,
    parse_main_program_session,
    parse_main_program_session_links,
    parse_official_accepted_papers,
)
from src.paths import ProjectPaths
from src.parser import ListingEntry
from src.screener import screen_papers
from src.utils import RAW_COLUMNS, write_csv


def _raw_row(
    paper_id: str,
    title: str,
    year: int = 2025,
    *,
    abstract: str = "A complete abstract without KD evidence.",
) -> dict[str, str]:
    return {
        "Paper_ID（论文编号）": paper_id,
        "Title（标题）": title,
        "Authors（作者）": "Alice Smith; Bob Jones",
        "Abstract（摘要）": abstract,
        "Venue（会议/期刊）": "CVPR",
        "Year（年份）": str(year),
        "Track（论文轨道）": "Main Conference",
        "Official_URL（官方论文页面）": f"https://openaccess.thecvf.com/content/CVPR{year}/html/{paper_id}.html",
        "PDF_URL（官方PDF链接）": f"https://openaccess.thecvf.com/content/CVPR{year}/papers/{paper_id}.pdf",
        "BibTeX（BibTeX信息）": (
            "@InProceedings{x,\n"
            "booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)},\n"
            f"year = {{{year}}}\n}}"
        ),
        "Crawl_Status（抓取状态）": "SUCCESS",
        "Crawl_Error（抓取错误）": "",
        "Notes（备注）": "",
    }


def _accepted(title: str, authors: str = "Alice Smith ⋅ Bob Jones") -> OfficialPaper:
    return OfficialPaper(title, authors, "https://cvpr.thecvf.com/accepted", "OFFICIAL_ACCEPTED_PAGE")


def _write_verified_gate(paths: ProjectPaths, frame: pd.DataFrame) -> None:
    write_csv(frame, paths.formal_corpus_csv, FORMAL_CORPUS_COLUMNS)
    paths.corpus_audit_status_json.write_text(
        json.dumps(
            {
                "status": COMPLETENESS_VERIFIED,
                "formal_corpus_sha256": file_sha256(paths.formal_corpus_csv),
            }
        ),
        encoding="utf-8",
    )


def test_official_accepted_parser_extracts_title_and_authors():
    page = """
    <html><body><table class="elc-table"><tr><th>Title</th></tr>
    <tr><td><strong>A &amp; B: Vision</strong><div class="indented"><i>Alice ⋅ Bob</i></div></td></tr>
    </table></body></html>
    """
    rows = parse_official_accepted_papers(page, "https://official.test/AcceptedPapers")
    assert [(row.title, row.authors) for row in rows] == [("A & B: Vision", "Alice ⋅ Bob")]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Ａ Model", "a model"),
        ("Vision &amp; Language", "vision & language"),
        ("A   Model\nfor Vision", "a model for vision"),
        ("Teacher–Student", "teacher-student"),
        ("‘Quoted’ Method", "'quoted' method"),
        ("A Method!", "a method"),
    ],
)
def test_conservative_title_normalization(left, right):
    assert normalize_title(left) == normalize_title(right)


def test_author_normalization_tolerates_cvf_order_hyphens_and_middle_initials():
    accepted = "Yichuan Huang ⋅ Vineeth Balasubramanian"
    cvf = "Huang, Yi-Chuan; Balasubramanian, Vineeth N"
    assert normalize_authors(accepted) == normalize_authors(cvf)


def test_program_fallback_parsers_only_main_poster_sessions():
    calendar = """
    <html><body>
      <a href="/virtual/2026/session/1">Poster Session 1</a>
      <a href="/virtual/2026/session/2">Findings Poster Session 1</a>
      <a href="/virtual/2026/session/3">Oral Session 1A</a>
    </body></html>
    """
    links = parse_main_program_session_links(calendar, "https://cvpr.thecvf.com/virtual/2026/calendar", 2026)
    assert links == [
        "https://cvpr.thecvf.com/virtual/2026/session/1",
        "https://cvpr.thecvf.com/virtual/2026/session/3",
    ]
    session = """
    <html><body><div class="track-schedule-card">
      <h5><strong><a href="/virtual/2026/poster/9">Formal Paper</a></strong></h5>
      <p class="text-muted">Alice ⋅ Bob</p>
    </div></body></html>
    """
    papers = parse_main_program_session(session, links[0], 2026)
    assert papers[0].title == "Formal Paper"
    assert papers[0].official_url.endswith("/virtual/2026/poster/9")


def test_cached_accepted_404_marker_uses_program_without_retrying_route(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    paths.accepted_page_unavailable_marker().parent.mkdir(parents=True, exist_ok=True)
    paths.accepted_page_unavailable_marker().write_text("404", encoding="utf-8")
    calendar_url = "https://cvpr.thecvf.com/virtual/2026/calendar"
    session_url = "https://cvpr.thecvf.com/virtual/2026/session/1"
    responses = {
        calendar_url: '<html><a href="/virtual/2026/session/1">Poster Session 1</a></html>',
        session_url: (
            '<html><div class="track-schedule-card"><h5><strong>'
            '<a href="/virtual/2026/poster/9">Formal Paper</a></strong></h5>'
            '<p class="text-muted">Alice ⋅ Bob</p></div></html>'
        ),
    }

    class DummyClient:
        def __init__(self):
            self.urls = []

        def get_text(self, url, cache_path, force=False):
            self.urls.append(url)
            return responses[url], "FIXTURE"

    client = DummyClient()
    papers, source = fetch_official_discovery_papers(client, paths, "CVPR", 2026)
    assert source == "OFFICIAL_MAIN_PROGRAM" and len(papers) == 1
    assert all("AcceptedPapers" not in url for url in client.urls)


def test_exact_normalized_probable_accepted_only_and_cvf_only_are_distinct():
    cvf = pd.DataFrame(
        [
            _raw_row("P1", "Teacher–Student Vision"),
            _raw_row("P2", "Camera Ready Title for Robust Vision"),
            _raw_row("P3", "CVF Only Formal Paper"),
        ]
    )
    accepted = [
        _accepted("Teacher-Student Vision"),
        _accepted("Camera-Ready Title for Robust Visual Recognition"),
        _accepted("Accepted Only Candidate"),
    ]
    records, presence, error = _match_records(cvf, accepted, "CVPR", 2025, probable_threshold=0.70)
    statuses = [record["Audit_Status"] for record in records]
    assert not error
    assert statuses.count(AUDIT_EXACT) == 1
    assert statuses.count(AUDIT_PROBABLE) == 1
    assert statuses.count(AUDIT_ACCEPTED_ONLY) == 1
    assert statuses.count(AUDIT_CVF_ONLY) == 1
    probable = next(record for record in records if record["Audit_Status"] == AUDIT_PROBABLE)
    assert probable["Formal_Publication_Status"] == "UNRESOLVED"
    assert presence["P1"] is True and presence["P2"] is True and presence["P3"] is False


def test_duplicate_accepted_and_cvf_titles_are_structural_errors():
    cvf = pd.DataFrame([_raw_row("P1", "Same"), _raw_row("P2", "Same")])
    records, _, error = _match_records(cvf, [_accepted("Other"), _accepted("Other")], "CVPR", 2025)
    assert error
    statuses = {record["Audit_Status"] for record in records}
    assert "DUPLICATE_ACCEPTED_TITLE" in statuses
    assert "DUPLICATE_CVF_TITLE" in statuses


def test_formal_cvf_and_ieee_enter_corpus_but_nonformal_does_not():
    cvf = pd.DataFrame([_raw_row("P1", "CVF Formal")])
    ieee = _raw_row("CVPR2025_SUPP_X", "IEEE Formal")
    ieee.update(
        {
            "Corpus_Source": "IEEE_XPLORE_SUPPLEMENT",
            "Formal_Publication_Status": FORMAL_STATUS_IEEE,
            "Formal_Publication_Evidence": "official record",
            "Formal_Publication_URL": "https://ieeexplore.ieee.org/document/1",
            "Original_CVF_Present": "FALSE",
            "Official_Accepted_Present": "TRUE",
            "Corpus_Audit_Notes": "",
        }
    )
    nonformal = dict(ieee, **{"Paper_ID（论文编号）": "NO", "Formal_Publication_Status": FORMAL_STATUS_NOT})
    formal = build_formal_corpus(cvf, {"P1": True}, [ieee, nonformal], "CVPR", 2025)
    assert set(formal["Paper_ID（论文编号）"]) == {"P1", "CVPR2025_SUPP_X"}
    assert set(formal["Formal_Publication_Status"]) == {FORMAL_STATUS_CVF, FORMAL_STATUS_IEEE}


def test_supplemental_paper_id_is_stable_unique():
    first = stable_supplemental_paper_id("CVPR", 2025, "IEEE", "doi:1")
    assert first == stable_supplemental_paper_id("CVPR", 2025, "IEEE", "doi:1")
    assert first != stable_supplemental_paper_id("CVPR", 2025, "IEEE", "doi:2")
    assert first.startswith("CVPR2025_SUPP_")


def test_audit_verified_with_complete_exact_fixture(monkeypatch, tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2025)
    write_csv([_raw_row("P1", "Formal Paper")], paths.raw_csv, RAW_COLUMNS)
    write_csv([], paths.crawl_exception_csv, ["error"])
    monkeypatch.setattr(
        audit_module,
        "fetch_official_discovery_papers",
        lambda *args, **kwargs: ([_accepted("Formal Paper")], "OFFICIAL_ACCEPTED_PAGE"),
    )
    result = audit_corpus(tmp_path, "CVPR", 2025, Path("config/source_config.yaml"))
    assert result.status == COMPLETENESS_VERIFIED
    assert result.counts["final_formal_corpus"] == 1
    assert paths.formal_corpus_csv.exists() and paths.formal_corpus_xlsx.exists()


def test_unresolved_and_metadata_incomplete_trigger_review(monkeypatch, tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2025)
    write_csv([_raw_row("P1", "Formal Paper", abstract="")], paths.raw_csv, RAW_COLUMNS)
    write_csv([], paths.crawl_exception_csv, ["error"])
    monkeypatch.setattr(
        audit_module,
        "fetch_official_discovery_papers",
        lambda *args, **kwargs: (
            [_accepted("Formal Paper"), _accepted("Unresolved Accepted")],
            "OFFICIAL_ACCEPTED_PAGE",
        ),
    )
    monkeypatch.setattr(audit_module, "_attempt_ieee_verification", lambda *args, **kwargs: None)
    monkeypatch.setattr(audit_module, "fetch_cvf_findings_entries", lambda *args, **kwargs: [])
    result = audit_corpus(tmp_path, "CVPR", 2025, Path("config/source_config.yaml"))
    assert result.status == COMPLETENESS_REVIEW
    assert result.counts["unresolved"] == 1
    assert result.counts["metadata_incomplete"] == 1


def test_official_findings_record_is_resolved_but_excluded_from_main(monkeypatch, tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2026)
    write_csv([_raw_row("P1", "Main Formal", year=2026)], paths.raw_csv, RAW_COLUMNS)
    write_csv([], paths.crawl_exception_csv, ["error"])
    monkeypatch.setattr(
        audit_module,
        "fetch_official_discovery_papers",
        lambda *args, **kwargs: (
            [_accepted("Main Formal"), _accepted("A Findings Paper")],
            "OFFICIAL_MAIN_PROGRAM",
        ),
    )
    monkeypatch.setattr(
        audit_module,
        "fetch_cvf_findings_entries",
        lambda *args, **kwargs: [
            ListingEntry(
                "A Findings Paper",
                "https://openaccess.thecvf.com/content/CVPR2026F/html/finding.html",
            )
        ],
    )
    result = audit_corpus(tmp_path, "CVPR", 2026, Path("config/source_config.yaml"))
    assert result.status == COMPLETENESS_VERIFIED
    assert result.counts["non_target_official_track"] == 1
    assert list(result.formal_corpus["Paper_ID（论文编号）"]) == ["P1"]


def test_screen_gate_requires_formal_corpus_and_verified_status(tmp_path):
    rules = Path("config/screening_rules_v1_2.yaml")
    with pytest.raises(FileNotFoundError, match="audit is required"):
        screen_papers(tmp_path, "CVPR", 2025, rules)
    paths = ProjectPaths(tmp_path, "CVPR", 2025)
    formal = pd.DataFrame([_raw_row("P1", "Formal Paper")]).reindex(columns=FORMAL_CORPUS_COLUMNS, fill_value="")
    write_csv(formal, paths.formal_corpus_csv, FORMAL_CORPUS_COLUMNS)
    paths.corpus_audit_status_json.write_text(
        json.dumps({"status": COMPLETENESS_REVIEW, "formal_corpus_sha256": file_sha256(paths.formal_corpus_csv)}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not verified"):
        screen_papers(tmp_path, "CVPR", 2025, rules)


def test_screen_reads_verified_formal_corpus_not_raw(tmp_path):
    paths = ProjectPaths(tmp_path, "CVPR", 2025)
    write_csv([_raw_row("RAW", "Raw Should Not Screen")], paths.raw_csv, RAW_COLUMNS)
    formal = pd.DataFrame([_raw_row("FORMAL", "Formal Screen Input")]).reindex(
        columns=FORMAL_CORPUS_COLUMNS, fill_value=""
    )
    _write_verified_gate(paths, formal)
    result = screen_papers(tmp_path, "CVPR", 2025, Path("config/screening_rules_v1_2.yaml"))
    assert list(result["Paper_ID（论文编号）"]) == ["FORMAL"]


def test_all_stops_before_screen_when_audit_requires_review(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "crawl_cvf", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(
        main_module,
        "audit_corpus",
        lambda **kwargs: SimpleNamespace(
            status=COMPLETENESS_REVIEW, report_path=tmp_path / "review.md"
        ),
    )
    monkeypatch.setattr(
        main_module,
        "screen_papers",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("screen must not run")),
    )
    assert main_module.main(["all", "--year", "2025", "--library-root", str(tmp_path)]) == 0


def test_v12_rules_file_hash_is_frozen():
    assert file_sha256(Path("config/screening_rules_v1_2.yaml")) == (
        "4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514"
    )
