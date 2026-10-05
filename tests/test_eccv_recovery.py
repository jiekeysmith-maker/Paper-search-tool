import json
import pytest
import requests
from test_pipeline_enumeration import Site,book,chapter
from venue_pipelines import Collection,eccv
from venue_audit import audit
from metadata_transport import RetryDeferred
from venue_runtime import FetchCache


def sources():
    doi='10.1007/978-1';url='https://link.springer.com/book/'+doi
    timeline=f'<li class="app-conference-series-timeline__item"><h3 data-test="bookTitle">Computer Vision – ECCV 2024</h3><a href="{url}">Computer Vision – ECCV 2024</a>'
    for label,n in [('Volumes',1),('Papers',3)]:
        timeline+=f'<p class="app-conference-series-timeline__item-count"><span class="app-conference-series-timeline__item-count-value">{n}</span><span class="app-conference-series-timeline__item-count-label">{label}</span></p>'
    links=[f'https://link.springer.com/chapter/{doi}_{n}' for n in (1,2,3)]
    pages={'https://link.springer.com/conference/eccv':timeline+'</li>',url:book(doi,'I',3,[1,2,3]),
           'https://www.ecva.net/papers.php':''.join(f'<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/{n}.html">Paper {n}</a></dt><dd><a>Alice</a></dd>' for n in (1,2,3)),
           **{u:chapter(doi,n) for n,u in enumerate(links,1)}}
    return pages,links


def test_complete_small_publisher_and_independent_evidence(tmp_path):
    pages,_=sources();c=Collection(Site(tmp_path,'ECCV',2024,pages));eccv(c.cache,c)
    assert c.evidence_complete and not c.issues
    assert all(r['Part']=='I' and r['LNCS_Volume'] for r in c.corpus)
    assert audit(tmp_path,'ECCV',2024,c.publisher,c.corpus,c.program,evidence_complete=c.evidence_complete,issues=c.issues)['status']=='VERIFIED'


@pytest.mark.parametrize('stop',[401,403,429,'deferred','auth','challenge'])
def test_access_stop_preserves_cached_chapters_without_further_network(tmp_path,stop):
    pages,links=sources();cache=Site(tmp_path,'ECCV',2024,pages)
    cache.get(links[2]);original=cache.fetch
    def fetch(url,**kwargs):
        if url==links[0]:
            cache.visited.append(url)
            if stop=='deferred':raise RetryDeferred('Retry-After 120s')
            if stop=='auth':raise ValueError('Non-official metadata URL: https://idp.springer.com/auth/login')
            response=requests.Response();response.url=url
            response.status_code=200 if stop=='challenge' else stop
            response._content=b'<title>Access Denied</title>'
            return response
        return original(url,**kwargs)
    cache.fetch=fetch;c=Collection(cache);eccv(cache,c)
    assert links[1] not in cache.visited and cache.visited.count(links[2])==1
    assert [r['Official_URL'] for r in c.corpus]==[links[2]]
    assert (tmp_path/'raw/Access_Restrictions.json').exists() and not c.evidence_complete
    result=audit(tmp_path,'ECCV',2024,c.publisher,c.corpus,c.program,evidence_complete=c.evidence_complete,issues=c.issues)
    assert result['status']=='REVIEW_REQUIRED' and not (tmp_path/'raw/Formal_Proceedings_Corpus.csv').exists()


def test_cache_only_overrides_refresh_and_never_fetches_misses(tmp_path):
    pages,links=sources();cache=Site(tmp_path,'ECCV',2024,pages);cache.get(links[0])
    cache.refresh_evidence=True
    assert cache.get(links[0],cache_only=True)
    with pytest.raises(ValueError,match='CACHE_ONLY_MISS'):cache.get(links[1],cache_only=True)
    assert cache.visited==[links[0]]
    cache.paths(links[0])[0].write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='INTEGRITY'):cache.get(links[0],cache_only=True)
    assert cache.visited==[links[0]]
