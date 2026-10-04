from pathlib import Path
import json
from hashlib import sha256
from ojs_adapter import AAAIOJSAdapter
from springer_adapter import SpringerECCVAdapter

ROOT=Path(__file__).parent/'fixtures/next'


def test_real_fixtures_integrity():
    for r in json.loads((ROOT/'provenance.json').read_text(encoding='utf-8')):
        assert sha256((ROOT/r['file']).read_bytes()).hexdigest()==r['fixture_sha256']


def test_actual_springer_double_space_part():
    html=(ROOT/'eccv_part_xviii.html').read_text(encoding='utf-8')
    r=SpringerECCVAdapter().book(html,'https://link.springer.com/book/10.1007/978-3-031-72649-1',2024)
    assert r['Part']=='XVIII' and r['LNCS_Volume']=='15076'
    assert r['Declared_Chapter_Count']==27
    assert len(r['Chapters']) < 27 and r['Pages']


def test_actual_robotics_scope_requires_explicit_intro():
    a=AAAIOJSAdapter(); html=(ROOT/'aaai_robots_section.html').read_text(encoding='utf-8')
    rows=a.index(html,'https://ojs.aaai.org/index.php/AAAI/issue/view/584',2024,38,9)
    assert len(rows)==16 and all(r['Scope']=='TARGET_MAIN' and r['Scope_Evidence'] for r in rows)
    rows=a.index(html.replace('AAAI Technical Track on Intelligent Robots','Unknown'), 'https://ojs.aaai.org/index.php/AAAI/issue/view/584',2024,38,9)
    assert all(r['Scope']=='UNCERTAIN' for r in rows)
    assert a.classify_scope('Intelligent Robots (ROB)')=='UNCERTAIN'
