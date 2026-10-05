import pytest
from aaai_oai_sets import list_sets, annual_records
from aaai_pipeline import ROOT, OAIIncomplete
from test_pipeline_enumeration import Site
from test_next_aaai import xml


def inventory(spec='AAAI:AI24-1',token=''):
    return f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListSets><set><setSpec>{spec}</setSpec><setName>AAAI Technical Track</setName></set><resumptionToken>{token}</resumptionToken></ListSets></OAI-PMH>'


def test_list_sets_pages_and_annual_record_chain(tmp_path):
    body=xml(1,'next',2,0).replace('</header>','<setSpec>AAAI:AI24-1</setSpec></header>')
    second=xml(1,'',2,1).replace('</header>','<setSpec>AAAI:AI24-1</setSpec></header>')
    pages={ROOT+'?verb=ListSets':inventory(token='more'),
           ROOT+'?verb=ListSets&resumptionToken=more':inventory('AAAI:AI25-1'),
           ROOT+'?verb=ListRecords&metadataPrefix=oai_dc&set=AAAI%3AAI24-1':body,
           ROOT+'?verb=ListRecords&resumptionToken=next':second}
    cache=Site(tmp_path,'AAAI',2024,pages)
    assert len(annual_records(cache,2024))==2
    assert len(cache.visited)==4
    assert 'COMPLETE' in (tmp_path/'raw/OAI_Annual_Chains.json').read_text()
    annual_records(cache,2024)
    assert len(cache.visited)==4


@pytest.mark.parametrize('second',[inventory('AAAI:AI25-1','more'),'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListSets/></OAI-PMH>'])
def test_bad_set_inventory_rejected(tmp_path,second):
    pages={ROOT+'?verb=ListSets':inventory(token='more'),ROOT+'?verb=ListSets&resumptionToken=more':second}
    with pytest.raises(ValueError):list_sets(Site(tmp_path,'AAAI',2024,pages))


def test_duplicate_target_set_rejected_without_discarding_inventory(tmp_path):
    pages={ROOT+'?verb=ListSets':inventory(token='more'),ROOT+'?verb=ListSets&resumptionToken=more':inventory()}
    with pytest.raises(OAIIncomplete,match='Duplicate target-year'):
        annual_records(Site(tmp_path,'AAAI',2024,pages),2024)
    assert (tmp_path/'raw/OAI_Set_Inventory.json').exists()


@pytest.mark.parametrize('change',['membership','volume','missing_token'])
def test_annual_chain_checks_scope_and_completion(tmp_path,change):
    body=xml(1,'',1,0).replace('</header>','<setSpec>AAAI:AI24-1</setSpec></header>')
    if change=='membership':body=body.replace('AAAI:AI24-1','AAAI:AI25-1')
    if change=='volume':body=body.replace('Vol. 38','Vol. 39')
    if change=='missing_token':body=body.replace('completeListSize="1"','completeListSize="2"')
    pages={ROOT+'?verb=ListSets':inventory(),ROOT+'?verb=ListRecords&metadataPrefix=oai_dc&set=AAAI%3AAI24-1':body}
    with pytest.raises(OAIIncomplete):annual_records(Site(tmp_path,'AAAI',2024,pages),2024)
    assert 'INCOMPLETE' in (tmp_path/'raw/OAI_Annual_Chains.json').read_text()


def test_active_and_deleted_same_annual_identity_requires_review(tmp_path):
    body=xml(1,'',2,0).replace('</header>','<setSpec>AAAI:AI24-1</setSpec></header>')
    body=body.replace('<resumptionToken','<record><header status="deleted"><identifier>id-0</identifier><setSpec>AAAI:AI24-1</setSpec></header></record><resumptionToken')
    pages={ROOT+'?verb=ListSets':inventory(),ROOT+'?verb=ListRecords&metadataPrefix=oai_dc&set=AAAI%3AAI24-1':body}
    with pytest.raises(OAIIncomplete,match='active/deleted'):
        annual_records(Site(tmp_path,'AAAI',2024,pages),2024)


def test_real_four_page_inventory_retains_historical_duplicate_sets(tmp_path):
    from pathlib import Path
    from hashlib import sha256
    import json
    fixtures=Path(__file__).parent/'fixtures/aaai_oai_sets'
    pages={}
    for r in json.loads((fixtures/'manifest.json').read_text(encoding='utf-8')):
        data=(fixtures/r['file']).read_bytes()
        assert sha256(data).hexdigest()==r['sha256']
        pages[r['url']]=data.decode('utf-8')
    rows=list_sets(Site(tmp_path,'AAAI',2024,pages))
    assert len(rows)==386
    assert len([r for r in rows if r['set_spec']=='AAAI:EAAI-POS'])==2
    assert any(r['set_spec']=='AAAI:2024-149' for r in rows)


@pytest.mark.parametrize('year',[2025,2026])
def test_failed_annual_group_keeps_partial_and_continues_next_group(tmp_path,year):
    import json
    prefix=f'AAAI:AI{str(year)[-2:]}'
    first,second=prefix+'-1',prefix+'-2'
    sets=inventory(first).replace('</ListSets>',f'<set><setSpec>{second}</setSpec><setName>Technical Track</setName></set></ListSets>')
    def record(spec):
        return xml(1,'',1,0).replace('Vol. 38',f'Vol. {year-1986}').replace('</header>',f'<setSpec>{spec}</setSpec></header>')
    broken=record(first).replace('completeListSize="1"','completeListSize="2"')
    last=record(second).replace('id-0','id-1').replace('/view/0','/view/1')
    urls=[ROOT+'?verb=ListRecords&metadataPrefix=oai_dc&set='+s.replace(':','%3A') for s in (first,second)]
    cache=Site(tmp_path,'AAAI',year,{ROOT+'?verb=ListSets':sets,urls[0]:broken,urls[1]:last})
    with pytest.raises(OAIIncomplete) as failed:annual_records(cache,year)
    assert len(failed.value.rows)==2 and urls[1] in cache.visited
    status=json.loads((tmp_path/'raw/OAI_Annual_Chains.json').read_text())
    assert status['status']=='INCOMPLETE'
    assert [c['status'] for c in status['chains']]==['INCOMPLETE','COMPLETE']
    assert 'truncated' in status['chains'][0]['error']
    # Partial official evidence never grants the formal screening gate.
    from venue_audit import audit
    from screen_verified import screen
    base=tmp_path/'gate'/'AAAI'/str(year)
    result=audit(base,'AAAI',year,[],[],failed.value.rows,evidence_complete=False,
                 issues=[dict(Status='OTHER_UNRESOLVED',Reason=str(failed.value))])
    assert result['status']=='REVIEW_REQUIRED' and result['unresolved_count']>0
    with pytest.raises(ValueError,match='gate'):screen('AAAI',year,library_root=tmp_path/'gate')


def test_deferred_request_does_not_continue_with_another_set(tmp_path,monkeypatch):
    import aaai_oai_sets
    from metadata_transport import RetryDeferred
    called=[]
    monkeypatch.setattr(aaai_oai_sets,'list_sets',lambda cache:[dict(set_spec='AAAI:AI25-'+str(i),name='Technical',evidence_url='fixture') for i in (1,2)])
    def deferred(cache,year,issues,*,set_spec):
        called.append(set_spec)
        raise OAIIncomplete(RetryDeferred('Retry-After 120s'),[])
    monkeypatch.setattr(aaai_oai_sets,'enumerate_oai',deferred)
    with pytest.raises(OAIIncomplete,match='Retry-After'):
        annual_records(Site(tmp_path,'AAAI',2025,{}),2025)
    assert called==['AAAI:AI25-1']


def test_deferred_oai_does_not_switch_to_article_detail(tmp_path,monkeypatch):
    import aaai_oai_sets
    from metadata_transport import RetryDeferred
    from venue_pipelines import aaai,Collection
    url='https://ojs.aaai.org/index.php/AAAI/issue/view/1'
    article='https://ojs.aaai.org/index.php/AAAI/article/view/1'
    link=f'<a href="{url}">Vol. 39 No. 1: AAAI-25 Technical Tracks</a>'
    issue=f'<h1>Vol. 39 No. 1: AAAI-25</h1><div class="section"><h2>AAAI Technical Track</h2><div class="obj_article_summary"><h3 class="title"><a href="{article}">Paper</a></h3><div class="authors">Alice</div></div></div>'
    cache=Site(tmp_path,'AAAI',2025,{'https://aaai.org/proceeding/aaai-39-2025/':link,
               'https://ojs.aaai.org/index.php/AAAI/issue/archive':link,url:issue})
    def deferred(*a):
        raise OAIIncomplete(OAIIncomplete(RetryDeferred('Retry-After 120s'),[]),[dict(Title='Paper',Official_URL=article)])
    monkeypatch.setattr(aaai_oai_sets,'annual_records',deferred)
    c=Collection(cache);aaai(cache,c)
    assert article not in cache.visited and not c.evidence_complete
    assert len(c.publisher)==1 and any('deferred' in r['Reason'] for r in c.issues)


def test_stable_url_does_not_hide_author_conflict(tmp_path,monkeypatch):
    import aaai_oai_sets
    from venue_pipelines import aaai,Collection
    from venue_audit import audit
    url='https://ojs.aaai.org/index.php/AAAI/issue/view/1'
    article='https://ojs.aaai.org/index.php/AAAI/article/view/1'
    link=f'<a href="{url}">Vol. 40 No. 1: AAAI-26 Technical Tracks</a>'
    issue=f'<h1>Vol. 40 No. 1: AAAI-26</h1><div class="section"><h2>AAAI Technical Track</h2><div class="obj_article_summary"><h3 class="title"><a href="{article}">Paper</a></h3><div class="authors">Alice</div></div></div>'
    cache=Site(tmp_path,'AAAI',2026,{'https://aaai.org/proceeding/aaai-40-2026/':link,
               'https://ojs.aaai.org/index.php/AAAI/issue/archive':link,url:issue})
    official=dict(Title='Paper',Authors='Bob',Official_URL=article,Volume=40,Issue=1,
                  DOI='10.1609/aaai.v40i1.1',Published_Date='2026-01-01',Abstract='Official abstract',Evidence_URL=ROOT)
    monkeypatch.setattr(aaai_oai_sets,'annual_records',lambda *a:[official])
    c=Collection(cache);aaai(cache,c)
    assert len(c.corpus)==1 and c.corpus[0]['Authors']=='Bob'
    assert c.issues[0]['Publisher_Authors']=='Alice' and c.issues[0]['OAI_Authors']=='Bob'
    result=audit(tmp_path,'AAAI',2026,c.publisher,c.corpus,c.program,evidence_complete=c.evidence_complete,issues=c.issues)
    assert result['status']=='REVIEW_REQUIRED' and result['unresolved_count']>0
