"""Public CSDL citation structure; no online-first date inferred from issue date."""
import html
import pytest
from tpami_adapter import TPAMIAdapter
from tpami_policy import TITLE
from test_tpami_public_metadata import public_metadata

URL='https://www.computer.org/csdl/journal/tp/2024/01/10274722/contentID'

def page(**changes):
    data=public_metadata()
    tags={'citation_journal_title':TITLE,'citation_issn':'0162-8828',
        'citation_title':data['title'],'citation_volume':'46','citation_issue':'01',
        'citation_publication_date':'2024/01/01','citation_doi':data['doi'],
        'og:url':URL,'description':data['abstract'],'og:description':data['abstract']}
    tags.update(changes)
    return ''.join(f'<meta name="{k}" content="{html.escape(v,quote=True)}">' for k,v in tags.items())+'<meta name="citation_author" content="Author">'

def context():
    return dict(Year=2024,Volume='46',Issue='1',Issue_Publication_Date='2024-01',
        Issue_URL='https://www.computer.org/csdl/journal/tp/2024/01',Native_Publisher_ID='10274722')

def test_public_csdl_metadata_retains_final_issue_without_invented_early_access():
    row=TPAMIAdapter().detail(page(),URL,context())
    assert row['Year']==2024 and row['Volume']=='46' and row['Issue']=='1'
    assert row['Official_URL']==URL and row['Abstract_Source_URL']==URL
    assert row['First_Official_Publication_Date']=='' and row['Early_Access_Date']==''

@pytest.mark.parametrize('changes',[
    {'citation_journal_title':'Other Journal'}, {'citation_issue':'02'},
    {'citation_publication_date':'2023/01/01'}, {'citation_doi':'10.1109/OTHER.1'},
    {'og:url':URL+'wrong'}, {'description':''}, {'og:description':'Different abstract'},
    {'citation_volume':'47'}, {'citation_title':'Correction to a method'},
])
def test_public_csdl_metadata_conflicts_fail_closed(changes):
    with pytest.raises(ValueError):TPAMIAdapter().detail(page(**changes),URL,context())

def test_public_csdl_inventory_document_identity_must_agree():
    ctx=context();ctx['Native_Publisher_ID']='999'
    with pytest.raises(ValueError,match='document identity'):TPAMIAdapter().detail(page(),URL,ctx)

def test_explicit_csdl_primary_keeps_metadata_but_missing_ieee_blocks_gate(tmp_path):
    from test_tpami_public_pages import annual
    from test_pipeline_enumeration import Site
    from tpami_pipeline import tpami
    from tpami_public_pages import csdl_annual_url
    from venue_pipelines import Collection
    from venue_audit import audit
    issue=context()['Issue_URL']
    pages={csdl_annual_url(2024):annual(2024,((1,'Jan.'),)),
        issue:f'''<h1>{TITLE}</h1>Volume 46 Issue 1 Showing 1 out of 1
        <div><a class="article-title" href="{URL}">Title</a>
        <div class="article-authors"><a>Author</a></div></div>''',URL:page(),
        'https://www.computer.org/robots.txt':'User-agent: *\nAllow: /',
        'https://ieeexplore.ieee.org/robots.txt':'User-agent: *\nAllow: /'}
    cache=Site(tmp_path,'TPAMI',2024,pages);col=Collection(cache)
    tpami(cache,col,primary_source='CSDL')
    assert len(col.publisher)==len(col.corpus)==1 and col.corpus[0]['Official_URL']==URL
    assert col.program==[] and not col.evidence_complete
    summary=audit(tmp_path,'TPAMI',2024,col.publisher,col.corpus,col.program,
        evidence_complete=col.evidence_complete,issues=col.issues)
    assert summary['status']=='REVIEW_REQUIRED'
    assert not (tmp_path/'raw/Formal_Proceedings_Corpus.csv').exists()
