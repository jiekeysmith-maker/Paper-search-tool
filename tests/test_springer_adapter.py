"""Synthetic parser contracts only; not a full ECCV completeness audit."""
import pytest
from springer_adapter import SpringerECCVAdapter

DOI = '10.1007/978-3-031-00000-0'
URL = 'https://link.springer.com/book/' + DOI
CHAPTER = 'https://link.springer.com/chapter/' + DOI + '_1'
BOOK = f'''<meta name="title" content="Computer Vision – ECCV 2024">
<meta name="doi" content="{DOI}">
<p>Proceedings, Part I; LNCS, volume 15000</p>
<div id="toc">Table of contents (2 papers)</div>
<div><div><a data-track="click_book_toc" href="{CHAPTER}">Example</a></div>
<span class="app-author-list">Alice</span></div>
<a href="{URL}?page=2">Next</a>'''


def detail():
    fields = dict(citation_inbook_title='Computer Vision – ECCV 2024',
                  citation_conference_abbrev='ECCV', citation_doi=DOI+'_1',
                  citation_abstract_html_url=CHAPTER, citation_author='Alice',
                  citation_firstpage='1', citation_publication_date='2025/01/01',
                  citation_title='Example')
    return ''.join(f'<meta name="{k}" content="{v}">' for k, v in fields.items()) + '<div id="Abs1-content">Abstract text</div>'


def test_book_reports_pagination_without_claiming_completeness():
    book = SpringerECCVAdapter().book(BOOK, URL, 2024)
    assert book['Declared_Chapter_Count'] == 2 and len(book['Chapters']) == 1
    assert book['Pages'] == [URL+'?page=2']
    assert book['Part'] == 'I' and book['LNCS_Volume'] == '15000'


def test_conference_year_independent_of_publication_date():
    row = SpringerECCVAdapter().detail(detail(), CHAPTER, 2024, DOI, 'fixture')
    assert row['Year'] == 2024 and row['Published_Date'].startswith('2025')
    assert row['DOI'] == DOI+'_1' and row['Abstract']


@pytest.mark.parametrize('old,new', [
    ('ECCV 2024', 'ECCV 2026'), ('ECCV 2024', 'ECCV 2024 Workshops'),
    ('Abstract text', ''), ('citation_author', 'missing_author'),
    ('citation_firstpage', 'missing_page'), ('citation_doi', 'missing_doi'),
])
def test_incomplete_or_wrong_scope_detail(old, new):
    with pytest.raises(ValueError):
        SpringerECCVAdapter().detail(detail().replace(old, new), CHAPTER, 2024, DOI, 'fixture')


def test_wrong_parent_rejected():
    with pytest.raises(ValueError):
        SpringerECCVAdapter().detail(detail(), CHAPTER, 2024, '10.1007/other', 'fixture')
    with pytest.raises(ValueError):
        SpringerECCVAdapter().book(BOOK.replace(CHAPTER, 'https://example.test/chapter/x'), URL, 2024)


def test_duplicate_and_wrong_year_book_rejected():
    with pytest.raises(ValueError):
        SpringerECCVAdapter().book(BOOK+f'<a data-track="click_book_toc" href="{CHAPTER}">Duplicate</a>', URL, 2024)
    with pytest.raises(ValueError):
        SpringerECCVAdapter().book(BOOK, URL, 2026)


def test_publisher_author_family_given_order_preserves_raw_metadata():
    import json
    row = SpringerECCVAdapter().detail(detail().replace('content="Alice"', 'content="Bonato, Jacopo"'), CHAPTER, 2024, DOI, 'fixture')
    assert row['Authors'] == 'Jacopo Bonato'
    assert json.loads(row['BibTeX_or_Publisher_Metadata'])['citation_author'] == ['Bonato, Jacopo']
