from pathlib import Path
import hashlib
from bs4 import BeautifulSoup
import pytest
from ojs_adapter import AAAIOJSAdapter
ROOT=Path(__file__).parent / "fixtures" / "snapshots"
INDEX='https://ojs.aaai.org/index.php/AAAI/issue/view/683'
DETAIL='https://ojs.aaai.org/index.php/AAAI/article/view/36958'
def read(u):return (ROOT/f'{hashlib.sha256(u.encode()).hexdigest()}.html').read_text(encoding='utf-8')
def test_real_fixture():
    a=AAAIOJSAdapter();r=a.index(read(INDEX),INDEX,2026,40,1);d=a.detail(read(DETAIL),DETAIL,2026,40,1,'fixture')
    assert r[0]['Title']==d['Title'] and d['Abstract'] and d['DOI']=='10.1609/aaai.v40i1.36958'
@pytest.mark.parametrize('section',['IAAI Technical Track','EAAI Main Track','AAAI Student Abstracts','AAAI Senior Member Presentations','AAAI Journal Track'])
def test_non_target(section):assert AAAIOJSAdapter().classify_scope(section)=='NON_TARGET'
def test_unknown():assert AAAIOJSAdapter().classify_scope('Unfamiliar Track')=='UNCERTAIN'
def test_year_volume_and_identity():
    a=AAAIOJSAdapter()
    for y,v,i in [(2025,40,1),(2026,39,1),(2026,40,2)]:
        with pytest.raises(ValueError):a.detail(read(DETAIL),DETAIL,y,v,i,'fixture')
    with pytest.raises(ValueError):a.detail(read(DETAIL),DETAIL.replace('36958','36959'),2026,40,1,'fixture')
def test_empty_abstract():
    s=BeautifulSoup(read(DETAIL),'html.parser');s.select_one('.item.abstract').clear()
    with pytest.raises(ValueError):AAAIOJSAdapter().detail(str(s),DETAIL,2026,40,1,'fixture')
def test_duplicate_index():
    s=BeautifulSoup(read(INDEX),'html.parser');links=s.select('.obj_article_summary h3.title a');links[1]['href']=links[0]['href']
    with pytest.raises(ValueError):AAAIOJSAdapter().index(str(s),INDEX,2026,40,1)
def test_missing_section():
    s=BeautifulSoup(read(INDEX),'html.parser');s.select_one('.section h2').decompose()
    with pytest.raises(ValueError):AAAIOJSAdapter().index(str(s),INDEX,2026,40,1)
def test_pagination():
    s=BeautifulSoup(read(INDEX),'html.parser');s.body.append(BeautifulSoup('<div class="cmp_pagination"><a class="next" href="?page=2">Next</a></div>','html.parser'))
    with pytest.raises(ValueError):AAAIOJSAdapter().index(str(s),INDEX,2026,40,1)
def test_same_title_different_authors():
    s=BeautifulSoup(read(INDEX),'html.parser');links=s.select('.obj_article_summary h3.title a');links[1].string=links[0].get_text()
    rows=AAAIOJSAdapter().index(str(s),INDEX,2026,40,1)
    assert rows[0]['Title']==rows[1]['Title'] and rows[0]['Authors']!=rows[1]['Authors'] and rows[0]['Native_Publisher_ID']!=rows[1]['Native_Publisher_ID']
