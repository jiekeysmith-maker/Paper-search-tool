import pytest
from eccv_identity import author_evidence,title_key,reconcile_eccv
from eccv_metadata import check_corpus


@pytest.mark.parametrize('a,b,grade',[
 ('Alice Smith; Bob Jones','Alice Smith; Bob Jones','AUTHOR_EXACT'),
 ('Alice Smith; Bob Jones','Bob Jones; Alice Smith','AUTHOR_SET_EXACT'),
 ('Frances Fengyi Yang','Frances F Yang','AUTHOR_INITIAL_COMPATIBLE'),
 ('M. Jehanzeb Mirza','Muhammad Jehanzeb Mirza','AUTHOR_INITIAL_COMPATIBLE'),
 ('Rareş Ambruş','Rareș A Ambruș','AUTHOR_INITIAL_COMPATIBLE'),
 ('Mert Bülent Sarıyıldız','Mert Bulent Sariyildiz','AUTHOR_EXACT'),
 ('Aswin C. Sankaranarayanan','Aswin Sankaranarayanan','AUTHOR_INITIAL_COMPATIBLE'),
 ('Alice Smith','Smith Alice','AUTHOR_SET_EXACT'),
 ('Alice Smith','Smith, Alice','AUTHOR_EXACT'),
 ('Liang Wang','Leon Wang','AUTHOR_CONFLICT'),
 ('Junrui Zhang','Arielle Zhang','AUTHOR_CONFLICT'),
 ('Alice Smith; Bob Jones','Alice Smith; Alice Smith','AUTHOR_CONFLICT'),
 ('Dmitry Tochilkin','Dmitrii Tochilkin','AUTHOR_CONFLICT'),
 ('A Smith; Alex Smith','Alex Smith; Andrew Smith','AUTHOR_INITIAL_COMPATIBLE'),
])
def test_author_evidence(a,b,grade):
    assert author_evidence(a,b)==grade


def row(title, authors='Alice Smith; Bob Jones; Carol Brown', url='https://example.org/a'):
    return dict(Title=title, Authors=authors, Official_URL=url)


@pytest.mark.parametrize('a,b',[
 ('Pre-trained Models','pretrained models'),
 ('A &amp;amp; B','A & B'),
 ('Model $$^2$$ with \\epsilon++','Model² with ε++'),
])
def test_title_format(a,b):
    assert title_key(a)==title_key(b)


def test_reordered_team_and_title():
    result,_=reconcile_eccv([row('Pre-trained Models')],[row('pretrained models','Carol Brown; Alice Smith; Bob Jones')])
    assert result[0]['Resolved']


@pytest.mark.parametrize('left,right',[
 ('Radiance Field Learners As UAV First-Person Viewers','UAV First-Person Viewers Are Radiance Field Learners'),
])
def test_unique_lexical_variant(left,right):
    result,_=reconcile_eccv([row(left)],[row(right,url='https://example.org/b')])
    assert result[0]['Resolved']


def test_radical_retitle_is_candidate_not_proof():
    result,_=reconcile_eccv([row('3D-Aware Text-Driven Talking Avatar Generation')],
        [row('Let the Avatar Talk using Texts without Paired Training Data',url='https://example.org/b')])
    assert not any(r['Resolved'] for r in result)


def test_michail_michael_not_alias():
    assert author_evidence('Michail Tarasiou','Michael Tarasiou')=='AUTHOR_CONFLICT'


def test_official_publication_link_supports_retitle_and_name_variant():
    from eccv_evidence import publication_link
    p=row('A diffusion model for localized editing','Alice Smith; Michail Adams; Carol Brown; David Young',
          'https://link.springer.com/chapter/10.1007/example_1')
    p['DOI']='10.1007/example_1'
    q=row('Localized human diffusion models','Alice Smith; Michael Adams; Carol Brown; David Young',
          'https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/1.html')
    html='<div id="content"><div id="papertitle">Localized human diffusion models</div><div id="abstract">An actual abstract.</div><a href="https://link.springer.com/chapter/10.1007/example_1">DOI</a></div>'
    q.update(publication_link(html,q['Official_URL'],2024,q['Title']))
    result,_=reconcile_eccv([p],[q])
    assert result[0]['Resolved'] and result[0]['Identity_Evidence']=='ECVA_EXPLICIT_DOI'


def test_reference_doi_and_wrong_year_are_not_publication_evidence():
    from eccv_evidence import publication_link
    url='https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/1.html'
    html='<div id="content"><div id="papertitle">A model</div><div id="abstract">See <a href="https://link.springer.com/chapter/10.1007/example_1">DOI</a></div></div>'
    with pytest.raises(ValueError):publication_link(html,url,2024,'A model')
    with pytest.raises(ValueError):publication_link(html,url,2026,'A model')


def test_title_does_not_override_author_conflict():
    result,_=reconcile_eccv([row('Prompt Quality','Liang Wang')],[row('Prompt Quality','Leon Wang')])
    assert not any(r['Resolved'] for r in result)


def test_competing_titles_fail_closed():
    result,_=reconcile_eccv([row('A Model',url='https://publisher.example/p')],[row('A Model'),row('A Model',url='https://example.org/b')])
    assert not any(r['Resolved'] for r in result)


def test_unmatched_independent_record_is_not_deleted():
    result,count=reconcile_eccv([], [row('Zero-shot Text-guided Infinite Image Synthesis with LLM guidance')])
    assert count==1 and result[0]['Status']=='ECVA_ONLY_UNRESOLVED'
    assert not result[0]['Resolved']


@pytest.mark.parametrize('abstract',['','Abstract unavailable','Please sign in to view chapter'])
def test_bad_abstract(abstract):
    r=dict(Title='A model',Abstract=abstract,Paper_ID='x',DOI='10.1007/example_1',Official_URL='https://link.springer.com/chapter/10.1007/example_1')
    issues,summary=check_corpus([r])
    assert issues and summary['abstract_present_count']==0


def test_usable_abstract_and_duplicate_identity():
    r=dict(Title='A model',Abstract='We introduce a model and evaluate its generalization on several benchmarks.',Paper_ID='x',DOI='10.1007/example_1',Official_URL='https://link.springer.com/chapter/10.1007/example_1')
    assert check_corpus([r])[0]==[]
    assert check_corpus([r,r])[1]['duplicate_identity_count']==2


@pytest.mark.parametrize('abstract,expected',[
 ('','REVIEW_REQUIRED'),
 ('Abstract unavailable','REVIEW_REQUIRED'),
 ('We present a model and evaluate its performance on benchmark datasets.','VERIFIED'),
])
def test_abstract_audit_gate(tmp_path,abstract,expected):
    from venue_audit import audit
    from screen_verified import verified_corpus
    r=dict(Title='A model',Abstract=abstract,Authors='Alice Smith',Paper_ID='10.1007/example_1',
           DOI='10.1007/example_1',Official_URL='https://link.springer.com/chapter/10.1007/example_1',
           Venue='ECCV',Year=2024,Track='Main Conference',PDF_URL='',Formal_Publication_Evidence='Official fixture')
    result=audit(tmp_path,'ECCV',2024,[r],[r],[r],evidence_complete=True)
    assert result['status']==expected
    if expected=='VERIFIED':
        assert (tmp_path/'raw/Formal_Proceedings_Corpus.csv').exists()
        verified_corpus(tmp_path,'ECCV',2024)
    else:
        assert not (tmp_path/'raw/Formal_Proceedings_Corpus.csv').exists()
        with pytest.raises(ValueError):verified_corpus(tmp_path,'ECCV',2024)
