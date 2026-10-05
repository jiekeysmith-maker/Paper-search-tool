"""Small complete fake official sites exercise adapters plus orchestrators offline."""
import json
from pathlib import Path
import pytest
import requests
import yaml

from venue_runtime import FetchCache
from venue_pipelines import Collection, icml, iclr, eccv, aaai, conference_program
from venue_audit import audit, reconcile
import run_venue


class Site(FetchCache):
    def __init__(self, tmp_path, venue, year, pages):
        self.visited = []
        def fetch(url, **kwargs):
            self.visited.append(url)
            assert url in pages, f'Unexpected request: {url}'
            response = requests.Response()
            response.status_code, response.url = 200, url
            response._content = pages[url].encode('utf-8')
            return response
        super().__init__(tmp_path, venue, year, fetch=fetch, delay=0)


def program(venue, year, title, authors, url):
    return json.dumps(dict(count=1, next=None, results=[dict(id=1, name=title,
        authors=[dict(fullname=a) for a in authors], decision='Accept (poster)',
        sourceurl=f'https://openreview.net/group?id={venue}.cc/{year}/Conference',
        virtualsite_url=f'/virtual/{year}/poster/1', paper_url=url)]))


def test_icml_bulk_metadata_full_pipeline_and_manual_reuse(tmp_path):
    root = 'https://proceedings.mlr.press/v267/'
    detail = root + 'x.html'
    meta = dict(URL=detail, id='x', volume=267, issued={'date-parts': [2025, 7, 1]},
                title='Knowledge Distillation', abstract='Teacher and student.',
                author=[dict(given='Alice', family='Smith')],
                **{'container-title': 'Proceedings of the 42nd International Conference on Machine Learning'})
    pages = {root: '<h1>International Conference on Machine Learning 2025</h1><div class="paper"><p class="title">Knowledge Distillation</p><span class="authors">Alice Smith</span><a href="x.html">abs</a></div>',
             root + 'assets/bib/citeproc.yaml': yaml.safe_dump([meta]),
             'https://icml.cc/static/virtual/data/icml-2025-orals-posters.json': program('ICML', 2025, 'Knowledge Distillation', ['Alice Smith'], detail)}
    cache = Site(tmp_path, 'ICML', 2025, pages)
    c = Collection(cache)
    icml(cache, c)
    assert len(c.corpus) == 1 and detail not in cache.visited
    result = audit(tmp_path, 'ICML', 2025, c.publisher, c.corpus, c.program, evidence_complete=c.evidence_complete)
    assert result['status'] == 'VERIFIED'
    icml(cache, Collection(cache))
    assert len(cache.visited) == 3


def test_iclr_history_snapshot_pipeline_matches_author_separators(tmp_path):
    fixtures = Path(__file__).parent / 'fixtures'
    manifest = json.loads((fixtures / 'manifest.json').read_text())
    detail_entry = next(e for e in manifest if 'iclr.cc' in e['url'] and '/hash/' in e['url'])
    from iclr_adapter import ICLRAdapter
    body = (fixtures / detail_entry['file']).read_text(encoding='utf-8')
    r = ICLRAdapter().detail(body, detail_entry['url'], 2024, 'offline')
    index = f'''<div class="book-title">International Conference on Learning Representations 2024</div>
    <div class="paper-count">1 papers</div><ul class="paper-list"><li data-track="conference"><div class="paper-content"><a href="{r['Official_URL']}">{r['Title']}</a></div><div class="paper-authors">{r['Authors']}</div></li></ul>'''
    pages = {'https://proceedings.iclr.cc/paper_files/paper/2024': index, r['Official_URL']: body,
             'https://iclr.cc/static/virtual/data/iclr-2024-orals-posters.json': program('ICLR', 2024, r['Title'], r['Authors'].split(', '), 'https://openreview.net/forum?id=fixture')}
    cache = Site(tmp_path, 'ICLR', 2024, pages)
    c = Collection(cache)
    iclr(cache, c)
    assert audit(tmp_path, 'ICLR', 2024, c.publisher, c.corpus, c.program, evidence_complete=c.evidence_complete)['status'] == 'VERIFIED'


def book(doi, part, count, chapters, links=''):
    return f'''<meta name="title" content="Computer Vision – ECCV 2024"><meta name="doi" content="{doi}">
    <p>Proceedings, Part {part}; LNCS, volume {15000 + len(part)}</p><div id="toc">Table of contents ({count} papers)</div>''' + ''.join(
        f'<div><div><a data-track="click_book_toc" href="https://link.springer.com/chapter/{doi}_{n}">Paper {n}</a></div><div class="app-author-list">Truncated et al.</div></div>' for n in chapters) + links


def chapter(doi, n):
    values = dict(citation_inbook_title='Computer Vision – ECCV 2024', citation_conference_abbrev='ECCV',
                  citation_doi=f'{doi}_{n}', citation_abstract_html_url=f'https://link.springer.com/chapter/{doi}_{n}',
                  citation_author='Alice', citation_firstpage='1', citation_publication_date='2025/01/01', citation_title=f'Paper {n}')
    return ''.join(f'<meta name="{k}" content="{v}">' for k, v in values.items()) + '<div id="Abs1-content">Real fixture abstract</div>'


def test_eccv_multivolume_pagination_and_full_authors(tmp_path):
    d1, d2 = '10.1007/978-1', '10.1007/978-2'
    u1, u2 = 'https://link.springer.com/book/' + d1, 'https://link.springer.com/book/' + d2
    pages = {'https://link.springer.com/conference/eccv': f'<a href="{u1}">Computer Vision – ECCV 2024</a><a href="{u2}">Computer Vision – ECCV 2024</a>',
             u1: book(d1, 'I', 2, [1], f'<a href="{u1}?page=1">1</a><a href="{u1}?page=2">2</a>'),
             u1 + '?page=2': book(d1, 'I', 2, [2]), u2: book(d2, 'II', 1, [3]),
             'https://www.ecva.net/papers.php': ''.join(f'<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/{n}.html">Paper {n}</a></dt><dd><a>Alice</a></dd>' for n in [1, 2, 3]),
             **{f'https://link.springer.com/chapter/{doi}_{n}': chapter(doi, n) for doi, n in [(d1, 1), (d1, 2), (d2, 3)]}}
    cache = Site(tmp_path, 'ECCV', 2024, pages)
    c = Collection(cache)
    eccv(cache, c)
    assert len(c.corpus) == 3 and all(r['Authors'] == 'Alice' for r in c.publisher)
    assert u1 + '?page=1' not in cache.visited
    # Matching linked books/chapters alone is not proof of a complete volume inventory.
    assert audit(tmp_path, 'ECCV', 2024, c.publisher, c.corpus, c.program, evidence_complete=c.evidence_complete, issues=c.issues)['status'] == 'REVIEW_REQUIRED'


def test_declared_count_does_not_prove_chapters_complete(tmp_path):
    doi = '10.1007/978-1'
    url = 'https://link.springer.com/book/' + doi
    pages = {'https://link.springer.com/conference/eccv': f'<a href="{url}">Computer Vision – ECCV 2024</a>',
             url: book(doi, 'I', 99, [1]), 'https://link.springer.com/chapter/' + doi + '_1': chapter(doi, 1),
             'https://www.ecva.net/papers.php': '<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/1.html">Paper 1</a></dt><dd><a>Alice</a></dd>'}
    cache = Site(tmp_path, 'ECCV', 2024, pages)
    c = Collection(cache)
    eccv(cache, c)
    assert c.issues
    assert audit(tmp_path, 'ECCV', 2024, c.publisher, c.corpus, c.program, evidence_complete=True, issues=c.issues)['status'] == 'REVIEW_REQUIRED'


def test_eccv_official_part_alias_recovers_missing_volume(tmp_path):
    d1,d2='10.1007/978-1','10.1007/978-2'
    u1,u2=('https://link.springer.com/book/'+d for d in (d1,d2))
    alias='https://link.springer.com/book/9780000000002'
    pages={'https://link.springer.com/conference/eccv':f'<a href="{u1}">Computer Vision – ECCV 2026</a>',
           'https://eccv.ecva.net/':f'<h1>ECCV 2026</h1><a href="{u1}">I</a><a href="{alias}">II</a>',
           u1:book(d1,'I',1,[1]).replace('2024','2026'),
           alias:book(d2,'II',2,[2],f'<a href="{u2}?page=1">1</a><a href="{u2}?page=2">2</a>').replace('2024','2026'),
           u2+'?page=2':book(d2,'II',2,[3]).replace('2024','2026'),
           'https://www.ecva.net/papers.php':''.join(f'<dt class="ptitle"><a href="papers/eccv_2026/papers_ECCV/html/{n}.html">Paper {n}</a></dt><dd><a>Alice</a></dd>' for n in (1,2,3)),
           **{f'https://link.springer.com/chapter/{d}_{n}':chapter(d,n).replace('2024','2026') for d,n in ((d1,1),(d2,2),(d2,3))}}
    cache=Site(tmp_path,'ECCV',2026,pages);c=Collection(cache)
    eccv(cache,c)
    assert len(c.corpus)==3
    assert cache.visited.count(alias)==1 and u2 not in cache.visited
    assert u2+'?page=1' not in cache.visited
    assert not any('Expected' in x['Reason'] or 'incomplete' in x['Reason'] for x in c.issues)


def test_missing_volume_does_not_discard_cached_chapter(tmp_path):
    doi='10.1007/978-1';url='https://link.springer.com/book/'+doi
    chapter_url='https://link.springer.com/chapter/'+doi+'_1'
    absent='https://link.springer.com/book/10.1007/unavailable'
    pages={'https://link.springer.com/conference/eccv':f'<a href="{url}">Computer Vision – ECCV 2024</a><a href="{absent}">Computer Vision – ECCV 2024</a>',
           url:book(doi,'I',1,[1]),chapter_url:chapter(doi,1),absent:'Temporary incomplete publisher response',
           'https://www.ecva.net/papers.php':'<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/1.html">Paper 1</a></dt><dd><a>Alice</a></dd>'}
    cache=Site(tmp_path,'ECCV',2024,pages);cache.get(chapter_url)
    c=Collection(cache);eccv(cache,c)
    assert len(c.corpus)==1 and not c.evidence_complete and c.issues
    assert cache.visited.count(chapter_url)==1


def test_screening_failure_preserves_verified_gate_for_manual_retry(tmp_path, monkeypatch):
    def success(cache, c):
        r = dict(Paper_ID='fixture', Title='Distillation', Abstract='Teacher and student', Authors='Alice',
                 Venue='ICLR', Year=2025, Track='Main Conference', Official_URL='https://proceedings.iclr.cc/fixture',
                 PDF_URL='', Formal_Publication_Evidence='Offline fixture')
        c.publisher, c.corpus, c.program, c.evidence_complete = [r], [r], [r], True
    original = run_venue.screen
    monkeypatch.setattr(run_venue, 'screen', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted screening')))
    assert run_venue.run_year('ICLR', 2025, tmp_path, pipeline=success)['status'] == 'FAILED'
    assert json.loads((tmp_path / 'ICLR/2025/raw/Audit_Summary.json').read_text())['status'] == 'VERIFIED'
    monkeypatch.setattr(run_venue, 'screen', original)
    assert run_venue.run_year('ICLR', 2025, tmp_path, pipeline=lambda *a: pytest.fail('recrawl'))['status'] == 'SCREENED_CSV_READY'


def test_aaai_two_independent_enumerations_and_section_scope(tmp_path):
    from bs4 import BeautifulSoup
    from ojs_adapter import AAAIOJSAdapter
    fixtures = Path(__file__).parent / 'fixtures'
    manifest = json.loads((fixtures / 'manifest.json').read_text())
    issue = next(e for e in manifest if '/issue/view/683' in e['url'])
    article = next(e for e in manifest if '/article/view/36958' in e['url'])
    body = (fixtures / article['file']).read_text(encoding='utf-8')
    r = AAAIOJSAdapter().detail(body, article['url'], 2026, 40, 1, 'offline')
    soup = BeautifulSoup((fixtures / issue['file']).read_text(encoding='utf-8'), 'html.parser')
    for item in soup.select('.obj_article_summary'):
        if not any(a.get('href') == article['url'] for a in item.select('a[href]')):
            item.decompose()
    url = 'https://ojs.aaai.org/index.php/AAAI/issue/view/683'
    link = f'<a href="{url}">Vol. 40 No. 1: AAAI-26 Technical Tracks 1</a>'
    import html
    xml = f'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <ListRecords><record><header/><metadata><dc:source>Proceedings of the AAAI Conference on Artificial Intelligence; Vol. 40 No. 1</dc:source>
    <dc:title>{html.escape(r['Title'])}</dc:title>{''.join('<dc:creator>'+html.escape(a)+'</dc:creator>' for a in r['Authors'].split('; '))}<dc:identifier>{article['url']}</dc:identifier></metadata></record></ListRecords></OAI-PMH>'''
    pages = {'https://aaai.org/proceeding/aaai-40-2026/': link,
             'https://ojs.aaai.org/index.php/AAAI/issue/archive': link,
             url: str(soup), article['url']: body,
             'https://ojs.aaai.org/index.php/AAAI/oai?verb=ListSets': '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListSets><set><setSpec>AAAI:AI26-1</setSpec><setName>AAAI Technical Track on Application Domains</setName></set></ListSets></OAI-PMH>',
             'https://ojs.aaai.org/index.php/AAAI/oai?verb=ListRecords&metadataPrefix=oai_dc&set=AAAI%3AAI26-1': xml.replace('<header/>','<header><setSpec>AAAI:AI26-1</setSpec></header>')}
    c = Collection(Site(tmp_path, 'AAAI', 2026, pages))
    aaai(c.cache, c)
    assert len(c.corpus) == 1
    assert audit(tmp_path, 'AAAI', 2026, c.publisher, c.corpus, c.program, evidence_complete=c.evidence_complete, issues=c.issues)['status'] == 'VERIFIED'


def test_oai_duplicates_not_misclassified_as_program_events():
    r = dict(Title='A', Authors='Alice', Official_URL='https://ojs.aaai.org/index.php/AAAI/article/view/1')
    rows, _ = reconcile([r], [r, r.copy()])
    assert any(x['Status'] == 'DUPLICATE_IDENTITY' and not x['Resolved'] for x in rows)


def test_springer_representative_book_is_not_entire_volume_list():
    from venue_pipelines import springer_declared_counts
    html = '''<li class="app-conference-series-timeline__item"><h3 data-test="bookTitle">Computer Vision – ECCV 2024</h3>
    <p class="app-conference-series-timeline__item-count"><span class="app-conference-series-timeline__item-count-value">89</span><span class="app-conference-series-timeline__item-count-label">Volumes</span></p></li>'''
    assert springer_declared_counts(html, 2024) == [{'Volumes': 89}]
    assert springer_declared_counts(html, 2026) == []


@pytest.mark.parametrize('cycle', [False, True])
def test_oai_resumption_enumerates_second_page_and_rejects_loop(tmp_path, cycle):
    from venue_pipelines import aaai_oai
    root = 'https://ojs.aaai.org/index.php/AAAI/oai'
    def page(number, token):
        return f'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <ListRecords><record><header/><metadata><dc:source>Proceedings of the AAAI Conference on Artificial Intelligence; Vol. 40 No. 1</dc:source><dc:title>Paper {number}</dc:title><dc:identifier>https://ojs.aaai.org/index.php/AAAI/article/view/{number}</dc:identifier></metadata></record><resumptionToken>{token}</resumptionToken></ListRecords></OAI-PMH>'''
    pages = {root + '?verb=ListRecords&metadataPrefix=oai_dc': page(1, 'cursor:2'),
             root + '?verb=ListRecords&resumptionToken=cursor%3A2': page(2, 'cursor:2' if cycle else '')}
    cache = Site(tmp_path, 'AAAI', 2026, pages)
    if cycle:
        with pytest.raises(ValueError, match='loop'):
            aaai_oai(cache, 2026, {1})
    else:
        assert [r['Title'] for r in aaai_oai(cache, 2026, {1})] == ['Paper 1', 'Paper 2']
    assert len(cache.visited) == 2
