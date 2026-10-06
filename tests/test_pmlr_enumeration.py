"""Offline publisher boundary entries, completeness gates and cache replay."""
import json
import pytest
import requests
import yaml
from venue_adapters import PMLRAdapter, independent_program
from venue_pipelines import Collection, icml
from venue_runtime import FetchCache
from venue_audit import audit

ROOT='https://proceedings.mlr.press/v267/'


def unit(key, tag='div', title='Boundary study'):
    return f'<{tag} class="paper"><p class="title">{title}</p><span class="authors">Alice Smith</span><a href="{key}.html">abs</a></{tag}>'


def page(body):
    return '<h1>International Conference on Machine Learning 2025</h1>'+body


def meta(key, title='Boundary study'):
    return dict(URL=ROOT+key+'.html',id=key,volume=267,issued={'date-parts':[[2025,1,1]]},
        title=title,abstract='Synthetic abstract.',author=[dict(given='Alice',family='Smith')],
        **{'container-title':'Proceedings of the 42nd International Conference on Machine Learning'})


def detail(title='Boundary study', year=2025):
    return f'<meta name="citation_title" content="{title}"><meta name="citation_author" content="Alice Smith"><div id="abstract">Synthetic abstract.</div><pre id="bibtex">year = {{{year}}}, volume = {{267}}</pre>'


def pages(index, records):
    return {ROOT:index,ROOT+'assets/bib/citeproc.yaml':yaml.safe_dump(records),
        'https://icml.cc/static/virtual/data/icml-2025-orals-posters.json':json.dumps(dict(count=1,next=None,results=[
            dict(id=1,name='Boundary study',authors=[dict(fullname='Alice Smith')],decision='Accept (poster)',
                 sourceurl='https://openreview.net/group?id=ICML.cc/2025/Position_Paper_Track',
                 virtualsite_url='/virtual/2025/poster/1',paper_url=ROOT+'a.html')]))}


def cache(tmp_path, site):
    def fetch(url,**kwargs):
        assert url in site, url
        r=requests.Response();r.status_code=200;r.url=url;r._content=site[url].encode()
        return r
    return FetchCache(tmp_path,'ICML',2025,fetch=fetch,delay=0)


def test_non_div_unit_and_normalization_collision_retained():
    rows=PMLRAdapter().index(page(unit('a')+unit('b','article')),ROOT,2025)
    assert len(rows)==2 and rows[0]['Title']==rows[1]['Title']


@pytest.mark.parametrize('body',[unit('a')+'<a href="b.html">abs</a>',
    unit('a').replace('class="authors"','class="unknown"'),
    unit('a')+unit('a'), unit('a')+'<div class="pagination">More entries</div>'])
def test_unparsed_or_ambiguous_entries_fail_closed(body):
    with pytest.raises(ValueError):PMLRAdapter().index(page(body),ROOT,2025)


def test_pagination_all_units_and_cached_replay(tmp_path):
    site=pages(page(unit('a')+'<a rel="next" href="?page=2">next</a>'),[meta('a'),meta('b','Other study')])
    site[ROOT+'?page=2']=page(unit('b','article','Other study')+'<a rel="prev" href="./">previous</a>')
    c=cache(tmp_path,site);first=Collection(c);icml(c,first)
    assert len(first.publisher)==len(first.corpus)==2 and first.evidence_complete
    requests_before=c.requests
    second=Collection(c);icml(c,second)
    assert c.requests==requests_before and second.publisher==first.publisher
    assert [(r['Title'],r['Official_URL']) for r in second.corpus]==[(r['Title'],r['Official_URL']) for r in first.corpus]


def test_citeproc_only_entry_requires_verified_detail(tmp_path):
    site=pages(page(unit('a')),[meta('a'),meta('b','Other study')])
    site[ROOT+'b.html']=detail('Other study')
    c=Collection(cache(tmp_path,site));icml(c.cache,c)
    assert c.evidence_complete and len(c.publisher)==2
    assert c.publisher[1]['Enumeration_Evidence']=='Citeproc-only record verified against official detail'


@pytest.mark.parametrize('broken',[detail('Other study',2024),detail('Conflicting title')])
def test_citeproc_only_invalid_evidence_stays_unresolved(tmp_path,broken):
    site=pages(page(unit('a')),[meta('a'),meta('b','Other study')]);site[ROOT+'b.html']=broken
    c=Collection(cache(tmp_path,site));icml(c.cache,c)
    assert len(c.publisher)==1 and c.issues and not c.evidence_complete
    result=audit(tmp_path,'ICML',2025,c.publisher,c.corpus,c.program,evidence_complete=c.evidence_complete,issues=c.issues)
    assert result['status']=='REVIEW_REQUIRED'


def test_bad_bulk_entry_recorded_not_silently_skipped(tmp_path):
    site=pages(page(unit('a')),[meta('a'),dict(title='Malformed entry')])
    c=Collection(cache(tmp_path,site));icml(c.cache,c)
    assert c.issues and 'Citeproc entry 2' in c.issues[0]['Reason'] and not c.evidence_complete


def test_duplicate_bulk_url_not_last_write_wins(tmp_path):
    site=pages(page(unit('a')),[meta('a'),meta('a','Conflicting title')]);site[ROOT+'a.html']=detail()
    c=Collection(cache(tmp_path,site));icml(c.cache,c)
    assert not c.evidence_complete and any(r['Status']=='DUPLICATE_IDENTITY' for r in c.issues)
    assert c.corpus[0]['Title']=='Boundary study'


def test_url_variants_same_resource():
    assert PMLRAdapter().publication_url('http://proceedings.mlr.press/v267/a.html?ref=x#abstract',ROOT)==ROOT+'a.html'


def test_position_track_is_not_excluded_by_name():
    data=json.loads(pages('',[])['https://icml.cc/static/virtual/data/icml-2025-orals-posters.json'])
    target,excluded=independent_program(data,'ICML',2025)
    assert len(target)==1 and not excluded


def test_index_parse_failure_enters_exception_queue(tmp_path):
    c=Collection(cache(tmp_path,pages(page(unit('a')+'<a href="unparsed.html">abs</a>'),[meta('a')])))
    with pytest.raises(ValueError,match='Unparsed'):icml(c.cache,c)
    assert c.issues and not c.evidence_complete


def test_pagination_cycle_fails_closed(tmp_path):
    site=pages(page(unit('a')+'<a rel="next" href="?page=2">next</a>'),[meta('a')])
    site[ROOT+'?page=2']=page(unit('b')+'<a rel="next" href="./">next</a>')
    c=Collection(cache(tmp_path,site))
    with pytest.raises(ValueError,match='cycle'):icml(c.cache,c)
    assert c.issues and not c.evidence_complete


def test_explicit_index_role_refreshes_html_index_units(tmp_path):
    url=ROOT+'index.html';site={url:'old'}
    c=cache(tmp_path,site);assert c.get(url,evidence=True)=='old'
    site[url]='new';c=cache(tmp_path,site);c.refresh_evidence=True
    assert c.get(url,evidence=True)=='new'
