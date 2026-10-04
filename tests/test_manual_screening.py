import hashlib
import json
from pathlib import Path
import pandas as pd
import pytest
import screen_verified as screening


@pytest.fixture
def corpus(tmp_path):
    raw = tmp_path / 'ICML' / '2024' / 'raw'
    raw.mkdir(parents=True)
    row = dict(Paper_ID='ICML2024_test', Title='Knowledge Distillation', Authors='Alice',
               Abstract='A teacher transfers knowledge to a student.', Venue='ICML',
               Year='2024', Track='Main Conference', Official_URL='https://example.test/paper',
               PDF_URL='', Formal_Publication_Evidence='Synthetic test evidence')
    path = raw / 'Formal_Proceedings_Corpus.csv'
    pd.DataFrame([row]).to_csv(path, index=False)
    audit = dict(venue='ICML', year=2024, status='VERIFIED', publisher_count=1,
                 unresolved_count=0, corpus_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (raw / 'Audit_Summary.json').write_text(json.dumps(audit), encoding='utf-8')
    return tmp_path, raw, row, audit


def test_validation_has_no_writes_and_explicit_write_is_local(corpus):
    root, raw, _, _ = corpus
    result = screening.screen('ICML', 2024, library_root=root)
    assert result['status'] == 'VALIDATED_NO_WRITE'
    out = raw.parent / 'screening'
    assert not out.exists()
    result = screening.screen('ICML', 2024, library_root=root, write=True)
    assert result['candidate_count'] == 1
    candidates = pd.read_csv(out / 'Needs_Secondary_Review.csv')
    assert candidates.Rules_SHA256.iloc[0] == screening.RULES_SHA256
    assert candidates.Review_Status.iloc[0] == 'PENDING_HUMAN_SECONDARY_REVIEW'
    assert len(list(out.glob('*.csv'))) == 7
    with pytest.raises(FileExistsError):
        screening.screen('ICML', 2024, library_root=root, write=True)


@pytest.mark.parametrize('change', [
    {'status': 'REVIEW_REQUIRED'}, {'venue': 'ICLR'}, {'year': 2025},
    {'publisher_count': 2}, {'unresolved_count': 1}, {'corpus_sha256': ''},
    {'corpus_path': '../outside.csv'},
])
def test_bad_audit_stops_before_writing(corpus, change):
    root, raw, _, audit = corpus
    audit.update(change)
    (raw / 'Audit_Summary.json').write_text(json.dumps(audit), encoding='utf-8')
    with pytest.raises(ValueError):
        screening.screen('ICML', 2024, library_root=root, write=True)
    assert not (raw.parent / 'screening').exists()


@pytest.mark.parametrize('column,value', [
    ('Abstract', ' '), ('Formal_Publication_Evidence', ''), ('Venue', 'ICLR'),
    ('Year', '2025'), ('Paper_ID', ''),
])
def test_invalid_corpus_even_with_matching_hash(corpus, column, value):
    root, raw, row, audit = corpus
    row[column] = value
    path = raw / 'Formal_Proceedings_Corpus.csv'
    pd.DataFrame([row]).to_csv(path, index=False)
    audit['corpus_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (raw / 'Audit_Summary.json').write_text(json.dumps(audit), encoding='utf-8')
    with pytest.raises(ValueError):
        screening.screen('ICML', 2024, library_root=root, write=True)
    assert not (raw.parent / 'screening').exists()


def test_changed_corpus_is_rejected(corpus):
    root, raw, _, _ = corpus
    path = raw / 'Formal_Proceedings_Corpus.csv'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='corpus changed'):
        screening.screen('ICML', 2024, library_root=root)


def test_changed_rules_is_rejected(corpus, monkeypatch):
    root, _, _, _ = corpus
    path = root / 'modified_rules.yaml'
    path.write_bytes(b'modified')
    monkeypatch.setattr(screening, 'RULES', path)
    with pytest.raises(ValueError, match='rules hash'):
        screening.screen('ICML', 2024, library_root=root)


@pytest.mark.parametrize('venue', ['CVPR', 'TPAMI', '../ICML'])
def test_unsupported_venue_has_no_io(tmp_path, venue):
    with pytest.raises(ValueError):
        screening.screen(venue, 2025, library_root=tmp_path, write=True)
    assert not list(tmp_path.iterdir())


def test_fixture_integrity():
    root = Path(__file__).parent / 'fixtures'
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8')):
        assert hashlib.sha256((root / entry['file']).read_bytes()).hexdigest() == entry['sha256']
