import pytest
from aaai_pipeline import enumerate_oai
from test_pipeline_enumeration import Site


def xml(count, token, expected, cursor):
    records = ''.join(f'<record><header><identifier>id-{cursor+n}</identifier></header><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:source>Proceedings of the AAAI Conference on Artificial Intelligence; Vol. 38 No. 1</dc:source><dc:title>Paper</dc:title><dc:identifier>https://ojs.aaai.org/index.php/AAAI/article/view/{cursor+n}</dc:identifier></metadata></record>' for n in range(count))
    return f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>{records}<resumptionToken completeListSize="{expected}" cursor="{cursor}">{token}</resumptionToken></ListRecords></OAI-PMH>'


@pytest.mark.parametrize('body,error', [(xml(1,'',2,0),'missing'), (xml(0,'next',2,0),'Empty'), (xml(1,'next',1,0),'beyond'), (xml(1,'next',2,1),'cursor')])
def test_oai_integrity(tmp_path, body, error):
    url = 'https://ojs.aaai.org/index.php/AAAI/oai?verb=ListRecords&metadataPrefix=oai_dc'
    cache = Site(tmp_path, 'AAAI', 2024, {url:body})
    with pytest.raises(ValueError, match=error):
        enumerate_oai(cache,2024,{1})
    assert 'INCOMPLETE' in (tmp_path/'raw/OAI_Pagination.json').read_text()


def test_partial_oai_preserved_on_http_failure(tmp_path):
    import requests
    from aaai_pipeline import OAIIncomplete, ROOT
    from venue_runtime import FetchCache
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        if len(calls)>1:
            raise requests.HTTPError('500 Server Error')
        response=requests.Response(); response.status_code=200;response.url=url
        response._content=xml(1,'next',2,0).encode()
        return response
    cache=FetchCache(tmp_path,'AAAI',2024,fetch=fetch,delay=0)
    with pytest.raises(OAIIncomplete) as error:
        enumerate_oai(cache,2024,{1})
    assert len(error.value.rows)==1
    assert isinstance(error.value.cause,requests.HTTPError)
    assert 'Paper' in (tmp_path/'raw/OAI_Annual_Records.csv').read_text(encoding='utf-8-sig')
    assert 'INCOMPLETE' in (tmp_path/'raw/OAI_Pagination.json').read_text()


def test_incomplete_evidence_keeps_metadata_but_blocks_screen(tmp_path,monkeypatch):
    from venue_pipelines import Collection
    from aaai_pipeline import quality_report
    from venue_runtime import FetchCache
    from run_venue import run_year
    from test_venue_pipeline import row
    import run_venue
    import json
    r=row('AAAI',2024);r['DOI']='10.1609/aaai.v38i1.1'
    def pipeline(cache,c):
        c.publisher=[r];c.corpus=[r];c.program=[r]
        c.issue('OAI continuation failed');c.evidence_complete=False
        quality_report(c)
    monkeypatch.setattr(run_venue,'screen',lambda *a,**k:pytest.fail('Gate bypass'))
    result=run_year('AAAI',2024,tmp_path,pipeline=pipeline)
    assert result['status']=='REVIEW_REQUIRED'
    base=tmp_path/'AAAI/2024'
    assert not (base/'raw/Formal_Proceedings_Corpus.csv').exists()
    q=json.loads((base/'reports/Corpus_Quality.json').read_text())
    assert q['metadata_complete_rows']==1 and not q['formal_screening_authorized']
    assert q['missing']['PDF_URL']==1


@pytest.mark.parametrize('conflict',[False,True])
def test_duplicate_export_preserves_evidence(tmp_path,conflict):
    import xml.etree.ElementTree as ET
    body=xml(1,'',2,0)
    tree=ET.fromstring(body);records=tree[0];record=ET.fromstring(ET.tostring(records[0]))
    if conflict:
        record.find('.//{http://purl.org/dc/elements/1.1/}title').text='Changed'
    records.insert(1,record)
    url='https://ojs.aaai.org/index.php/AAAI/oai?verb=ListRecords&metadataPrefix=oai_dc'
    rows=enumerate_oai(Site(tmp_path,'AAAI',2024,{url:ET.tostring(tree,encoding='unicode')}),2024,{1})
    assert len(rows)==(2 if conflict else 1)
    evidence=(tmp_path/'raw/OAI_Duplicate_Evidence.csv').read_text(encoding='utf-8-sig')
    assert ('CONFLICTING_DUPLICATE_IDENTITY' if conflict else 'EXACT_DUPLICATE_EXPORT') in evidence
