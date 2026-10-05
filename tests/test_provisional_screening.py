import json
import pandas as pd
import pytest
from screen_provisional import screen
from screen_verified import verified_corpus, MAPPING, RULES
from src.screener import RuleEngine
from src.utils import load_yaml


def corpus(tmp_path):
    base=tmp_path/'ECCV/2024';raw=base/'raw';raw.mkdir(parents=True)
    row=dict(Paper_ID='ECCV2024:test',Title='Knowledge distillation',Authors='A. Author',
             Abstract='We train a student with knowledge distillation from a teacher.',Venue='ECCV',
             Year='2024',Track='Main Conference',Official_URL='https://link.springer.com/chapter/10.1007/test',
             PDF_URL='',Formal_Publication_Evidence='Official chapter DOI')
    pd.DataFrame([row]).to_csv(raw/'PROVISIONAL_Formal_Proceedings_Corpus.csv',index=False)
    (raw/'Audit_Summary.json').write_text(json.dumps(dict(venue='ECCV',year=2024,status='REVIEW_REQUIRED',unresolved_count=2)))
    return base,row


def test_explicit_provisional_preserves_audit_and_formal_gate(tmp_path):
    base,row=corpus(tmp_path);original=(base/'raw/Audit_Summary.json').read_bytes()
    result=screen('ECCV',2024,library_root=tmp_path,write=True)
    assert result['provisional_screening'] and result['unresolved_count']==2
    assert result['screened_count']==1
    out=pd.read_csv(base/'screening_provisional/KD_Screening.csv')
    expected=RuleEngine(load_yaml(RULES)).evaluate({v:row[k] for k,v in MAPPING.items()})
    assert out.iloc[0]['Decision（筛选决定）']==expected['Decision（筛选决定）']
    assert (base/'raw/Audit_Summary.json').read_bytes()==original
    assert not (base/'screening').exists()
    with pytest.raises(ValueError,match='gate'):verified_corpus(base,'ECCV',2024)
    with pytest.raises(FileExistsError):screen('ECCV',2024,library_root=tmp_path,write=True)


def test_missing_abstract_retained_but_not_screened(tmp_path):
    base,row=corpus(tmp_path)
    missing={**row,'Paper_ID':'missing','Official_URL':row['Official_URL']+'2','Abstract':''}
    pd.DataFrame([row,missing]).to_csv(base/'raw/PROVISIONAL_Formal_Proceedings_Corpus.csv',index=False)
    result=screen('ECCV',2024,library_root=tmp_path,write=True)
    assert result['input_count']==2 and result['metadata_excluded_count']==1
    assert pd.read_csv(base/'screening_provisional/Metadata_Excluded_From_Screening.csv').iloc[0].Paper_ID=='missing'


@pytest.mark.parametrize('problem',['duplicate','year','status','formal','empty'])
def test_provisional_rejects_ambiguous_or_wrong_inputs(tmp_path,problem):
    base,row=corpus(tmp_path);raw=base/'raw'
    if problem=='duplicate':pd.DataFrame([row,row]).to_csv(raw/'PROVISIONAL_Formal_Proceedings_Corpus.csv',index=False)
    if problem=='year':
        row['Year']='2026';pd.DataFrame([row]).to_csv(raw/'PROVISIONAL_Formal_Proceedings_Corpus.csv',index=False)
    if problem=='status':(raw/'Audit_Summary.json').write_text(json.dumps(dict(venue='ECCV',year=2024,status='VERIFIED',unresolved_count=0)))
    if problem=='formal':(raw/'Formal_Proceedings_Corpus.csv').write_text('existing')
    if problem=='empty':
        row['Abstract']='';pd.DataFrame([row]).to_csv(raw/'PROVISIONAL_Formal_Proceedings_Corpus.csv',index=False)
    with pytest.raises(ValueError):screen('ECCV',2024,library_root=tmp_path,write=True)
    assert not (base/'screening_provisional').exists()
