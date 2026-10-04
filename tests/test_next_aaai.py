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
