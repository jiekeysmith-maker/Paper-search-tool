import pytest
from tpami_adapter import TPAMIAdapter
from tpami_policy import TITLE, YEAR_BASIS, corpus_issues, admission
from venue_runtime import write_csv
from venue_pipelines import Collection
from tpami_pipeline import tpami
from test_pipeline_enumeration import Site


def context(year=2024):
    return dict(Year=year, Volume=str(year-1978), Issue='5',
                Issue_Publication_Date=f'{year}-05', Issue_URL='https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=123')


def metadata():
    return dict(publication_title=TITLE, publication_number=34, content_type='Journals',
                volume='46', issue='5', publication_year=2024, doi='10.1109/TPAMI.2023.3275249',
                article_number=10122995, authors=['Author One'], title='Title', abstract='Abstract',
                publication_date='11 May 2023', early_access_date='2023-05-11')


def test_final_issue_year_not_early_access():
    r=TPAMIAdapter().metadata(metadata(),context())
    assert r['Year']==2024 and r['Early_Access_Date']=='2023-05-11'
    assert r['Year_Basis']==YEAR_BASIS


@pytest.mark.parametrize('field,value', [('abstract',''),('authors',[]),('doi',''),('volume',''),
    ('publication_year',2023),('issue','6'),('content_type','Early Access'),('title','')])
def test_metadata_contract(field,value):
    data=metadata(); data[field]=value
    with pytest.raises(ValueError): TPAMIAdapter().metadata(data,context())


def test_duplicate_doi_within_and_across_years(tmp_path):
    base=tmp_path/'TPAMI/2024'; r=TPAMIAdapter().metadata(metadata(),context())
    assert corpus_issues(base,[r,r])[0]['Status']=='DUPLICATE_IDENTITY'
    write_csv(tmp_path/'TPAMI/2025/raw/Formal_Proceedings_Corpus.csv',[r])
    assert corpus_issues(base,[r])[0]['Status']=='DUPLICATE_IDENTITY'


def test_year_date_conflict(tmp_path):
    r=TPAMIAdapter().metadata(metadata(),context()); r['Issue_Publication_Date']='2023-05'
    assert corpus_issues(tmp_path/'2024',[r])


def test_cross_year_admission(tmp_path):
    lock=tmp_path/'2025/runtime/run.lock'; lock.parent.mkdir(parents=True); lock.write_text('active')
    with pytest.raises(RuntimeError): admission(tmp_path/'2024')


def test_volume_directory_explicit_final_issue():
    html=f'<h1>{TITLE} 2024</h1><div>Volume 46 Issue 5 2024-05 <a href="?isnumber=123">Issue</a></div>'
    rows=TPAMIAdapter().volume_directory(html,'https://ieeexplore.ieee.org/xpl/RecentIssue.jsp',2024)
    assert rows[0]['Volume']=='46' and rows[0]['Issue']=='5'


def test_public_spa_fails_closed(tmp_path):
    pages={'https://ieeexplore.ieee.org/robots.txt':'User-agent: *\nAllow: /',
           'https://www.computer.org/robots.txt':'User-agent: *\nAllow: /',
           'https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=34':'<app-root></app-root>',
           'https://www.computer.org/csdl/journal/tp/2024':'<app-root></app-root>'}
    cache=Site(tmp_path,'TPAMI',2024,pages); c=Collection(cache); tpami(cache,c)
    assert not c.evidence_complete and len(c.issues)==2 and not c.corpus


def test_issue_pagination_count_rejected():
    html=f'{TITLE} Volume 46 Issue 5 2 Articles <a href="https://ieeexplore.ieee.org/document/123">Title</a>'
    with pytest.raises(ValueError,match='pagination'): TPAMIAdapter().issue_index(html,'https://ieeexplore.ieee.org/',context())
