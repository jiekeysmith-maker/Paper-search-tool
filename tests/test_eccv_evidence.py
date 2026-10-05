import pytest
from eccv_evidence import conference_volumes, correction_evidence, accepted_program


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


def test_accepted_program_is_never_final_publication_proof():
    row='<tr><td><a href="/virtual/2026/poster/12">A paper</a><div class="indented"><i>Alice ⋅ Bob</i></div></td></tr>'
    html='<h1>ECCV 2026 Accepted Papers</h1><p>This paper list is preliminary pending publisher checks.</p><table id="event-list-2026-poster-nodates-filter-vslinks-table">'+row+'</table>'
    records,status=accepted_program(html,'https://eccv.ecva.net/Conferences/2026/AcceptedPapers',2026)
    assert status['preliminary'] and not status['final_publication_inventory']
    assert records[0]['Authors']=='Alice; Bob' and records[0]['Official_URL'].endswith('/poster/12')
    repeated,evidence=accepted_program(html.replace(row,row*2),'https://eccv.ecva.net/',2026)
    assert len(repeated)==2 and evidence['unique_event_count']==1 and evidence['duplicate_event_ids']==['12']
    from venue_audit import reconcile
    compared,_=reconcile([],repeated)
    assert compared[0]['Status']=='DUPLICATE_EVENT' and compared[0]['Resolved']
    with pytest.raises(ValueError):accepted_program(html,'https://eccv.ecva.net/',2024)


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
