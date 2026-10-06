"""Offline contracts, not a claim that current public SPA pages expose these fixtures."""
import json
import pytest
import requests
from test_tpami import context,metadata
from test_pipeline_enumeration import Site
from tpami_adapter import TPAMIAdapter,NonResearchArticle
from tpami_policy import normalize_date,corpus_issues,TITLE
from tpami_pipeline import tpami
from venue_pipelines import Collection
from venue_audit import audit
from tpami_public_pages import csdl_annual_url


def public_site():
    issue=context()['Issue_URL'];cs='https://www.computer.org/csdl/journal/tp/2024/05'
    root='https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=34'
    doc='https://ieeexplore.ieee.org/document/10122995'
    def annual(url):return f'<h1>{TITLE} 2024</h1>1 Issues<div>Volume 46 Issue 5 2024-05 <a href="{url}">Issue</a></div>'
    toc=f'{TITLE} Volume 46 Issue 5 1 Articles <a href="{doc}">Title</a>'
    cs_annual=f'''<h1>{TITLE}</h1>1 Issues<div id="pastIssuesMenu"><div class="past-issue-panel">
        <a class="cover-image-link" href="{cs}" aria-label="Year 2024, Issue Number 05"></a>
        <h3>May 2024</h3></div></div>'''
    return {root:annual(issue),csdl_annual_url(2024):cs_annual,
        issue:toc,cs:toc,doc:'<script>xplGlobal.document.metadata = '+json.dumps(metadata())+';</script>',
        'https://ieeexplore.ieee.org/robots.txt':'User-agent: *\nAllow: /',
        'https://www.computer.org/robots.txt':'User-agent: *\nAllow: /'},issue,cs,doc


@pytest.mark.parametrize('raw,normalized',[('11 May 2023','2023-05-11'),('May 2024','2024-05'),('2024','2024'),('2024-02-31',''),('unknown','')])
def test_date_precision(raw,normalized):assert normalize_date(raw)==normalized


def test_one_discovered_issue_is_not_forced_to_twelve(tmp_path):
    pages,_,_,_=public_site();c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert c.evidence_complete and len(c.corpus)==1
    summary=audit(tmp_path,'TPAMI',2024,c.publisher,c.corpus,c.program,evidence_complete=c.evidence_complete,issues=c.issues)
    assert summary['status']=='VERIFIED'
    assert c.corpus[0]['First_Official_Publication_Date']=='2023-05-11'


def test_matching_partial_inventories_do_not_prove_completeness(tmp_path):
    pages,_,_,_=public_site();pages={u:s.replace('1 Issues','') for u,s in pages.items()}
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.corpus)==1 and not c.evidence_complete and c.issues


def test_independent_unavailable_preserves_publisher_metadata(tmp_path):
    pages,_,_,_=public_site();pages[csdl_annual_url(2024)]='<app-root/>'
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.corpus)==1 and not c.evidence_complete


def test_pagination_collects_partial_rows_before_missing_page(tmp_path):
    pages,issue,_,_=public_site()
    pages[issue]=pages[issue].replace('1 Articles','2 Articles')+f'<a rel="next" href="{issue}&page=2">Next</a>'
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.corpus)==1 and not c.evidence_complete
    report=json.loads((tmp_path/'raw/IEEE_Source_Enumeration.json').read_text())
    assert not report['issues'][0]['complete'] and report['issues'][0]['total_post_dedup']==1


def test_real_pagination_contract(tmp_path):
    pages,issue,cs,doc=public_site();doc2=doc[:-1]+'6'
    pages[issue]=pages[issue].replace('1 Articles','2 Articles')+f'<a rel="next" href="{issue}&page=2">Next</a>'
    pages[issue+'&page=2']=f'{TITLE} Volume 46 Issue 5 2 Articles <a href="{doc2}">Second Title</a>'
    pages[cs]=pages[cs].replace('1 Articles','2 Articles')+f'<a href="{doc2}">Second Title</a>'
    data=metadata();data.update(article_number=10122996,title='Second Title',doi='10.1109/TPAMI.2024.1')
    pages[doc2]='<script>xplGlobal.document.metadata='+json.dumps(data)+';</script>'
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.corpus)==2 and c.evidence_complete


def test_official_nonresearch_exclusion_checks_document_identity(tmp_path):
    pages,_,_,doc=public_site();data=metadata();data['article_type']='Editorial';data['abstract']=''
    pages[doc]='<script>xplGlobal.document.metadata='+json.dumps(data)+';</script>'
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.excluded)==1 and not c.publisher and not c.program
    with pytest.raises(ValueError,match='identity mismatch'):
        TPAMIAdapter().detail(pages[doc],doc[:-1]+'6',context())


@pytest.mark.parametrize('abstract',['Abstract','<p></p>','Please sign in to view this article'])
def test_placeholder_not_metadata(abstract):
    data=metadata();data['abstract']=abstract
    with pytest.raises(ValueError):TPAMIAdapter().metadata(data,context())


def test_citation_metadata_static_page():
    tags=dict(journal_title=TITLE,issn='0162-8828',title='Title',author='Author One',doi='10.1109/TPAMI.2023.1',
              volume='46',issue='5',publication_date='2024-05',online_date='2023-11-12',abstract='We propose and evaluate a learning method.')
    html=''.join(f'<meta name="citation_{k}" content="{v}">' for k,v in tags.items())
    r=TPAMIAdapter().detail(html,'https://ieeexplore.ieee.org/document/123',context())
    assert r['Year']==2024 and r['First_Official_Publication_Date']=='2023-11-12'


def test_issue_thirteen_not_rejected_by_assumed_schedule(tmp_path):
    ctx=context();ctx['Issue']='13';data=metadata();data['issue']='13'
    r=TPAMIAdapter().metadata(data,ctx)
    assert not corpus_issues(tmp_path/'2024',[r])


def test_early_and_final_events_same_doi_do_not_duplicate_corpus(tmp_path):
    pages,issue,cs,doc=public_site();early=doc[:-1]+'6'
    for u in (issue,cs):pages[u]=pages[u].replace('1 Articles','2 Articles')+f'<a href="{early}">Early Title</a>'
    data=metadata();data.update(article_number=10122996,volume='',issue='',content_type='Early Access')
    pages[early]='<script>xplGlobal.document.metadata='+json.dumps(data)+';</script>'
    c=Collection(Site(tmp_path,'TPAMI',2024,pages));tpami(c.cache,c)
    assert len(c.corpus)==1 and not c.evidence_complete
    events=json.loads((tmp_path/'raw/Publication_Events.json').read_text())
    assert {r['Event_Type'] for r in events}=={'FINAL_ISSUE_ASSIGNMENT','EARLY_ACCESS_UNASSIGNED'}
    assert len({r['DOI'] for r in events})==1


@pytest.mark.parametrize('code',[401,403,429,202])
def test_access_failure_stops_network_and_preserves_cached_document(tmp_path,code):
    pages,issue,cs,doc=public_site();doc2=doc[:-1]+'6'
    for u in (issue,cs):pages[u]=pages[u].replace('1 Articles','2 Articles')+f'<a href="{doc2}">Second Title</a>'
    data=metadata();data.update(article_number=10122996,title='Second Title',doi='10.1109/TPAMI.2024.1')
    pages[doc2]='<script>xplGlobal.document.metadata='+json.dumps(data)+';</script>'
    cache=Site(tmp_path,'TPAMI',2024,pages);cache.get(doc2);original=cache.fetch
    def fail(url,**kwargs):
        if url==doc:
            cache.visited.append(url);r=requests.Response();r.status_code=code;r.url=url;r._content=b'';return r
        return original(url,**kwargs)
    cache.fetch=fail;c=Collection(cache);tpami(cache,c)
    assert len(c.corpus)==1 and c.corpus[0]['Native_Publisher_ID']=='10122996'
    assert cache.visited.count(doc2)==1 and not c.evidence_complete
