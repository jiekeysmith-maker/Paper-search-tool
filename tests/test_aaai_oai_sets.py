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
