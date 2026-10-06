"""Small structural fixtures based on public CSDL DOM observed 2026-10-06."""
import pytest
from tpami_adapter import TPAMIAdapter
from tpami_public_pages import csdl_annual_url
from tpami_policy import TITLE


def annual(year,items=((1,'Jan.'),(3,'Mar.'))):
    return f'<h1>{TITLE}</h1><div id="pastIssuesMenu">'+''.join(
        f'<div class="past-issue-panel"><a class="cover-image-link" '
        f'aria-label="Year {year}, Issue Number {n:02}" '
        f'href="/csdl/journal/tp/{year}/{n:02}"></a><h3>{month} {year}</h3></div>'
        for n,month in items)+'</div>'


def issue(rows=1,total=1):
    return f'<h1>{TITLE}</h1>Volume 46 Issue 1 Showing {rows} out of {total}'+''.join(
        f'<div><a class="article-title" href="/csdl/journal/tp/2024/01/{100+i}/abc{i}">Title {i}</a>'
        '<div class="article-authors"><a>Jane Doe</a><a>John Smith</a></div></div>' for i in range(rows))


@pytest.mark.parametrize('year',[2024,2025,2026])
def test_real_archive_route_dynamic_noncontiguous_issues(year):
    a=TPAMIAdapter();html=annual(year);url=csdl_annual_url(year)
    assert f'/past-issues/2020/{year}' in url
    rows=a.volume_directory(html,url,year)
    assert [r['Issue'] for r in rows]==['1','3']
    assert all(r['Volume']=='' for r in rows)  # No year-minus-offset guess.
    assert not a.inventory_evidence(html,rows)['complete']


@pytest.mark.parametrize('html',['<app-root></app-root>','404 Page Not Found'])
def test_shell_or_404_is_not_empty_year(html):
    with pytest.raises(ValueError):TPAMIAdapter().volume_directory(html,csdl_annual_url(2024),2024)


def test_annual_label_date_conflict_and_duplicate_rejected():
    a=TPAMIAdapter();url=csdl_annual_url(2024)
    for html in (annual(2024).replace('Jan. 2024','Jan. 2023'),
                 annual(2024,((1,'Jan.'),(1,'Jan.'))),
                 annual(2024).replace('Number 01','Number 02')):
        with pytest.raises(ValueError):a.volume_directory(html,url,2024)


def test_archive_journal_identity_may_be_accessibility_label():
    html=annual(2024).replace(f'<h1>{TITLE}</h1>',f'<nav aria-label="Periodical Navigation Menu for {TITLE}">IEEE TPAMI</nav>')
    assert len(TPAMIAdapter().volume_directory(html,csdl_annual_url(2024),2024))==2
    with pytest.raises(ValueError):
        TPAMIAdapter().volume_directory(html.replace(TITLE,'Another Journal'),csdl_annual_url(2024),2024)


def test_real_september_abbreviation():
    rows=TPAMIAdapter().volume_directory(annual(2024,((9,'Sept.'),)),csdl_annual_url(2024),2024)
    assert rows[0]['Issue_Publication_Date']=='2024-09'


def test_independent_article_identity_and_authors_preserved():
    a=TPAMIAdapter();ctx=a.volume_directory(annual(2024),csdl_annual_url(2024),2024)[0]
    html=issue()+'<a href="/csdl/journal/tp/2025/01/999/xyz">Trending</a>'
    page=a.issue_page(html,ctx['Issue_URL'],ctx)
    assert len(page['rows'])==1 and page['context']['Volume']=='46'
    assert ctx['Volume']==''  # Parsing does not mutate caller evidence.
    r=page['rows'][0]
    assert r['Native_Publisher_ID']=='100' and r['Authors']=='Jane Doe; John Smith'
    assert r['Independent_Source_URL'].startswith('https://www.computer.org/')


def test_partial_rendered_issue_is_not_complete():
    a=TPAMIAdapter();ctx=a.volume_directory(annual(2024),csdl_annual_url(2024),2024)[0]
    page=a.issue_page(issue(1,2),ctx['Issue_URL'],ctx)
    assert len(page['rows'])==1 and page['declared_count']==2
    with pytest.raises(ValueError,match='pagination'):a.issue_index(issue(1,2),ctx['Issue_URL'],ctx)


def test_duplicate_and_missing_author_not_silently_accepted():
    a=TPAMIAdapter();ctx=a.volume_directory(annual(2024),csdl_annual_url(2024),2024)[0]
    for html in (issue(2,2).replace('/101/','/100/'),issue().replace('article-authors','other')):
        with pytest.raises(ValueError):a.issue_page(html,ctx['Issue_URL'],ctx)
