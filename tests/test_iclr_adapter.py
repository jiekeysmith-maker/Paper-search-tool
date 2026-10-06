import hashlib,json
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from iclr_adapter import ICLRAdapter
from venue_adapters import independent_program

ROOT=Path(__file__).parent / "fixtures"
URL='https://proceedings.iclr.cc/paper_files/paper/2024'
DETAIL=URL+'/hash/00153f90d9177dd3a872133972bc8dea-Abstract-Conference.html'
def snapshot(u):return (ROOT/'snapshots'/f'{hashlib.sha256(u.encode()).hexdigest()}.html').read_text(encoding='utf-8')

def test_real_index_detail():
    a=ICLRAdapter();rows,excluded=a.index(snapshot(URL),URL,2024)
    d=a.detail(snapshot(DETAIL),DETAIL,2024,'fixture')
    assert rows[0]['Title']==d['Title'] and d['Abstract'] and d['Published_Date'].startswith('2024')
    assert not excluded

@pytest.mark.parametrize('url,year',[(DETAIL,2025),(DETAIL.replace('proceedings.iclr.cc','example.com'),2024),(DETAIL.replace('Conference','Workshop'),2024)])
def test_wrong_identity(url,year):
    with pytest.raises(ValueError):ICLRAdapter().detail(snapshot(DETAIL),url,year,'fixture')

def test_wrong_index_year():
    with pytest.raises(ValueError):ICLRAdapter().index(snapshot(URL),URL,2025)

def test_empty_abstract():
    soup=BeautifulSoup(snapshot(DETAIL),'html.parser');soup.select_one('.paper-abstract').clear()
    with pytest.raises(ValueError):ICLRAdapter().detail(str(soup),DETAIL,2024,'fixture')

def test_duplicate_url():
    soup=BeautifulSoup(snapshot(URL),'html.parser');links=soup.select('.paper-content a');links[1]['href']=links[0]['href']
    with pytest.raises(ValueError):ICLRAdapter().index(str(soup),URL,2024)

def test_missing_index_row():
    soup=BeautifulSoup(snapshot(URL),'html.parser');soup.select_one('.paper-list li').decompose()
    with pytest.raises(ValueError):ICLRAdapter().index(str(soup),URL,2024)

def test_non_target_track_is_recorded():
    soup=BeautifulSoup(snapshot(URL),'html.parser');soup.select_one('.paper-list li')['data-track']='workshop'
    rows,excluded=ICLRAdapter().index(str(soup),URL,2024)
    assert len(excluded)==1 and len(rows)+1==len(soup.select('.paper-list li'))

def test_same_title_different_author_not_deduplicated():
    soup=BeautifulSoup(snapshot(URL),'html.parser');links=soup.select('.paper-content a');links[1].string=links[0].get_text()
    rows,_=ICLRAdapter().index(str(soup),URL,2024)
    assert rows[0]['Title']==rows[1]['Title'] and rows[0]['Authors']!=rows[1]['Authors']
    assert rows[0]['Native_Publisher_ID']!=rows[1]['Native_Publisher_ID']

def test_program_scope_and_pagination():
    rows=[dict(sourceurl='https://openreview.net/group?id=ICLR.cc/2024/Conference',decision='Accept: Poster'),dict(sourceurl='https://openreview.net/group?id=ICLR.cc/2024/Workshop',decision='Accept: Poster'),dict(sourceurl='https://openreview.net/group?id=ICLR.cc/2024/Conference',decision='Reject')]
    accepted,excluded=independent_program(dict(count=3,results=rows),'ICLR',2024)
    assert len(accepted)==1 and len(excluded)==2
    with pytest.raises(ValueError):independent_program(dict(count=4,results=rows),'ICLR',2024)


def test_structured_citation_author_preserves_repeated_name():
    soup=BeautifulSoup(snapshot(DETAIL),'html.parser')
    for tag in soup.select('meta[name="citation_author"]'):tag.decompose()
    tag=soup.new_tag('meta',attrs={'name':'citation_author','content':'Example, Example'})
    soup.head.append(tag)
    soup.select_one('.paper-authors').string='Example'
    result=ICLRAdapter().detail(str(soup),DETAIL,2024,'fixture')
    assert result['Authors']=='Example Example'
    assert result['Authors_As_Displayed']=='Example'
