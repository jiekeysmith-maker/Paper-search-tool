import pytest
from eccv_evidence import conference_volumes, correction_evidence


def test_real_correction_notices_keep_original_identity():
    import json
    from pathlib import Path
    from hashlib import sha256
    root=Path(__file__).parent/'fixtures/eccv_corrections'
    for item in json.loads((root/'provenance.json').read_text()):
        data=(root/item['file']).read_bytes()
        assert sha256(data).hexdigest()==item['fixture_sha256']
        proof=correction_evidence(data.decode(),item['url'],2024)
        assert proof and proof['Original_Chapter_URL'].endswith('_16')
        assert proof['DOI'] in item['url']


def test_official_isbn_part_inventory():
    html='<h1>ECCV 2026</h1><a href="https://link.springer.com/book/9783032377173">LXVI</a>'
    assert conference_volumes(html,'https://eccv.ecva.net/',2026)[0]['Part']=='LXVI'
    with pytest.raises(ValueError):conference_volumes(html*2,'https://eccv.ecva.net/',2026)
    with pytest.raises(ValueError):conference_volumes(html,'https://eccv.ecva.net/',2024)


def test_correction_requires_explicit_original_and_c_page():
    html=''.join(f'<meta name="{k}" content="{v}">' for k,v in {
        'citation_title':'Correction to: Test','citation_doi':'10.1007/test_28',
        'citation_firstpage':'C1','citation_inbook_title':'Computer Vision – ECCV 2024'}.items())
    html+='<p>The updated version of this chapter can be found at https://doi.org/10.1007/test_16</p>'
    url='https://link.springer.com/chapter/10.1007/test_28'
    assert correction_evidence(html,url,2024)['Publication_Type']=='CORRECTION_NOTICE'
    assert correction_evidence(html.replace('C1','1'),url,2024) is None
    assert correction_evidence(html.replace('test_16','test_28'),url,2024) is None
    assert correction_evidence(html,url,2026) is None
