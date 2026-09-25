from src.parser import parse_cvf_detail, parse_cvf_listing, parse_cvf_workshop_index


def test_parse_listing_filters_track_and_deduplicates():
    html = """
    <dl>
      <dt class="ptitle"><br><a href="/content/CVPR2026/html/A_CVPR_2026_paper.html">Paper A</a></dt>
      <dt class="ptitle"><a href="/content/CVPR2026/html/A_CVPR_2026_paper.html">Paper A</a></dt>
      <dt class="ptitle"><a href="/content/CVPR2026F/html/B_CVPRF_2026_paper.html">Paper B</a></dt>
    </dl>
    """
    entries = parse_cvf_listing(html, "https://openaccess.thecvf.com", "/content/CVPR2026/html/")
    assert len(entries) == 1
    assert entries[0].title == "Paper A"
    assert entries[0].official_url.startswith("https://openaccess.thecvf.com/")


def test_parse_detail_extracts_official_metadata():
    html = """
    <html><head>
      <meta name="citation_title" content="A KD Paper">
      <meta name="citation_author" content="Doe, Jane">
      <meta name="citation_author" content="Li, Ming">
      <meta name="citation_pdf_url" content="https://openaccess.thecvf.com/content/CVPR2026/papers/a.pdf">
    </head><body>
      <div id="abstract">We transfer knowledge from a teacher to a student.</div>
      <div class="bibref pre-white-space">@InProceedings{Doe_2026_CVPR}</div>
    </body></html>
    """
    result = parse_cvf_detail(html, "https://openaccess.thecvf.com/content/CVPR2026/html/a.html")
    assert result["title"] == "A KD Paper"
    assert result["authors"] == "Doe, Jane; Li, Ming"
    assert "teacher to a student" in result["abstract"]
    assert result["pdf_url"].endswith("a.pdf")
    assert "InProceedings" in result["bibtex"]


def test_parse_workshop_index():
    html = """
    <a href="/CVPR2026_workshops/EDGE">Efficient On-Device Generation</a>
    <a href="/CVPR2026_workshops/EDGE">duplicate</a>
    <a href="/CVPR2026?day=all">Main papers</a>
    """
    workshops = parse_cvf_workshop_index(
        html, "https://openaccess.thecvf.com", "/CVPR2026_workshops/"
    )
    assert len(workshops) == 1
    assert workshops[0].name == "Efficient On-Device Generation"
    assert workshops[0].listing_url.endswith("/CVPR2026_workshops/EDGE")
