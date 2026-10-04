import json
from pathlib import Path
import pytest
from venue_adapters import PMLRAdapter, independent_program
from hashlib import sha256
from screen_verified import RULES

def digest(data): return sha256(data).hexdigest()
FIXTURES = Path(__file__).parent / "fixtures"
from src.screener import RuleEngine
from src.utils import load_yaml

def fixture(url): return (FIXTURES/'snapshots'/(digest(url.encode())+'.html')).read_text(encoding='utf-8')
def test_real_pmlr_fixture():
    a=PMLRAdapter(); u='https://proceedings.mlr.press/v235/'
    index=a.index(fixture(u),u,2024)
    d=a.detail(fixture(index[0]['Official_URL']),index[0]['Official_URL'],2024,235)
    assert d['title']==index[0]['Title'] and d['abstract'] and d['authors']
def sample(): return dict(URL='https://proceedings.mlr.press/v235/x.html',volume=235,issued={'date-parts':[2024,7,8]},**{'container-title':'Proceedings of the 41st International Conference on Machine Learning'},id='x',title='X',abstract='',author=[])
@pytest.mark.parametrize('change',[{'volume':267},{'issued':{'date-parts':[2025,1,1]}},{'container-title':'ICML Workshop'},{'URL':'https://evil.test/v235/x.html'}])
def test_scope_negative(change):
    r=sample();r.update(change);assert PMLRAdapter().classify_scope(r,2024,235)=='NON_TARGET'
def test_missing_abstract_is_preserved():
    r=PMLRAdapter().normalize(sample(),2024,235,'fixture','now');assert r['Abstract']==''
def test_missing_index_rejected():
    with pytest.raises(ValueError): PMLRAdapter().index('<h1>International Conference on Machine Learning 2024</h1>','https://proceedings.mlr.press/v235/',2024)
def test_duplicate_url_rejected_same_title_different_identity_preserved():
    item='<div class="paper"><p class="title">Same</p><span class="authors">A</span><a href="a.html">abs</a></div>'
    head='<h1>International Conference on Machine Learning 2024</h1>'
    a=PMLRAdapter()
    with pytest.raises(ValueError):a.index(head+item+item,'https://proceedings.mlr.press/v235/',2024)
    assert len(a.index(head+item+item.replace('a.html','b.html').replace('>A<','>B<'),'https://proceedings.mlr.press/v235/',2024))==2
def test_program_pagination_and_non_target():
    with pytest.raises(ValueError): independent_program(dict(count=1,next='page2',results=[]),'ICML',2024)
    target,non=independent_program(dict(count=1,next=None,results=[{'sourceurl':'JMLR','decision':'Accept'}]),'ICML',2024)
    assert not target and len(non)==1
def test_frozen_engine_no_platform_dependency():
    assert digest(RULES.read_bytes())=='4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514'
    engine=RuleEngine(load_yaml(RULES));p={'Title（标题）':'Knowledge Distillation','Abstract（摘要）':'A teacher transfers knowledge to a student.'}
    left=engine.evaluate(p);right=engine.evaluate({**p,'Venue（会议/期刊）':'ICML'})
    left.pop('Venue（会议/期刊）');right.pop('Venue（会议/期刊）')
    assert left==right
