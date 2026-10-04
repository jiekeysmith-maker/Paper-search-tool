import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import requests

import run_venue as runner
from venue_runtime import FetchCache, job_lock, write_json, write_csv
from venue_audit import audit, reconcile
from venue_pipelines import Collection, icml, iclr, aaai_issue_links, ecva_index, aaai_oai
from screen_verified import screen, RULES, RULES_SHA256


def row(venue='ICLR', year=2025):
    return dict(Paper_ID=f'{venue}{year}_1', Title='Knowledge Distillation', Authors='Alice',
                Abstract='A teacher transfers knowledge to a student.', Venue=venue, Year=year,
                Track='Main Conference', Official_URL=f'https://proceedings.iclr.cc/{year}/1',
                PDF_URL='', Formal_Publication_Evidence='Synthetic offline fixture')


def success(cache, collection):
    r = row(cache.venue, cache.year)
    collection.publisher = [r]
    collection.corpus = [r]
    collection.program = [r]
    collection.evidence_complete = True


def response(url, status=200, body=b'valid metadata'):
    result = requests.Response()
    result.status_code, result._content, result.url = status, body, url
    return result


def test_success_atomic_screen_and_preserve_existing(tmp_path):
    result = runner.run_year('ICLR', 2025, tmp_path, pipeline=success)
    assert result['status'] == 'SCREENED_CSV_READY'
    base = tmp_path / 'ICLR/2025'
    candidate = base / 'screening/Needs_Secondary_Review.csv'
    before = candidate.read_bytes()
    assert runner.run_year('ICLR', 2025, tmp_path, pipeline=lambda *a: pytest.fail('refetched'))['status'] == 'ALREADY_COMPLETE'
    assert candidate.read_bytes() == before
    candidate.write_bytes(before + b'changed')
    assert runner.run_year('ICLR', 2025, tmp_path)['status'] == 'FAILED'
    assert candidate.read_bytes() == before + b'changed'


def test_jobs_are_isolated(tmp_path):
    for venue, year in [('ICLR', 2024), ('ICLR', 2025), ('AAAI', 2024)]:
        assert runner.run_year(venue, year, tmp_path, pipeline=success)['status'] == 'SCREENED_CSV_READY'
    assert not (tmp_path / 'runtime').exists()
    assert len(list(tmp_path.glob('*/*/runtime/Progress.json'))) == 3


def test_two_actual_processes_and_duplicate_lock(tmp_path):
    script = ('import sys; from pathlib import Path; from venue_runtime import job_lock; '
              'ctx=job_lock(Path(sys.argv[1]),sys.argv[2],2025); ctx.__enter__(); '
              'print("READY",flush=True); input(); ctx.__exit__(None,None,None)')
    cwd = Path(__file__).resolve().parents[1]
    processes = []
    try:
        for venue in ('ICML', 'ICLR'):
            p = subprocess.Popen([sys.executable, '-B', '-c', script, str(tmp_path / venue / '2025'), venue],
                                 cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(p)
            assert p.stdout.readline().strip() == 'READY'
        duplicate = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path / 'ICML/2025'), 'ICML'],
                                   cwd=cwd, input='\n', capture_output=True, text=True, timeout=15)
        assert duplicate.returncode != 0 and 'JOB_LOCKED' in duplicate.stderr
        assert all(p.poll() is None for p in processes)
        locks = list(tmp_path.glob('*/*/runtime/run.lock'))
        assert len(locks) == 2
        for lock in locks:
            assert {'pid', 'start_time', 'venue', 'year', 'hostname'} <= json.loads(lock.read_text()).keys()
    finally:
        for p in processes:
            p.communicate('\n', timeout=15)


def test_stale_lock_never_silently_removed(tmp_path):
    path = tmp_path / 'runtime/run.lock'
    write_json(path, dict(pid=-1, start_time='old', venue='ICLR', year=2025))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='JOB_LOCKED'):
        with job_lock(tmp_path, 'ICLR', 2025):
            pass
    assert path.read_bytes() == before


def test_order_review_and_failure_continue(tmp_path):
    visited = []
    def run(venue, year, root):
        visited.append(year)
        if year == 2025:
            raise RuntimeError('network failed')
        return dict(status='REVIEW_REQUIRED')
    results = runner.run_years('ICLR', [2026, 2025, 2024], tmp_path, runner=run)
    assert visited == [2026, 2025, 2024]
    assert [r['status'] for r in results] == ['REVIEW_REQUIRED', 'FAILED', 'REVIEW_REQUIRED']


def test_review_never_screens(tmp_path, monkeypatch):
    def unverified(cache, c):
        success(cache, c)
        c.evidence_complete = False
    monkeypatch.setattr(runner, 'screen', lambda *a, **k: pytest.fail('gate bypassed'))
    assert runner.run_year('ICLR', 2024, tmp_path, pipeline=unverified)['status'] == 'REVIEW_REQUIRED'
    assert not (tmp_path / 'ICLR/2024/raw/Formal_Proceedings_Corpus.csv').exists()


def test_failed_fetch_saves_evidence_and_next_year(tmp_path):
    def broken(cache, c):
        success(cache, c)
        raise requests.ConnectionError('interrupted')
    def run(v, y, root):
        return runner.run_year(v, y, root, pipeline=broken if y == 2024 else success)
    results = runner.run_years('ICLR', [2024, 2025], tmp_path, runner=run)
    assert [r['status'] for r in results] == ['FAILED', 'SCREENED_CSV_READY']
    assert (tmp_path / 'ICLR/2024/raw/PROVISIONAL_Formal_Proceedings_Corpus.csv').exists()
    assert not (tmp_path / 'ICLR/2024/screening').exists()


def test_missing_corpus_no_screen(tmp_path):
    raw = tmp_path / 'ICLR/2024/raw'
    write_json(raw / 'Audit_Summary.json', dict(venue='ICLR', year=2024, status='VERIFIED', unresolved_count=0))
    with pytest.raises(FileNotFoundError):
        screen('ICLR', 2024, library_root=tmp_path)


def test_interrupted_fetch_manual_reuse_and_hash_validation(tmp_path):
    url1, url2 = 'https://iclr.cc/a', 'https://iclr.cc/b'
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if url == url2:
            raise requests.ConnectionError('interrupted')
        return response(url)
    c = FetchCache(tmp_path, 'ICLR', 2024, fetch=fetch, delay=0)
    assert c.get(url1) == 'valid metadata'
    with pytest.raises(requests.ConnectionError):
        c.get(url2)
    c = FetchCache(tmp_path, 'ICLR', 2024, fetch=fetch, delay=0)
    assert c.get(url1) == 'valid metadata' and calls.count(url1) == 1
    c.paths(url1)[0].write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='INTEGRITY'):
        c.get(url1)


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_http_failures_never_cached(tmp_path, status):
    url = 'https://iclr.cc/a'
    c = FetchCache(tmp_path, 'ICLR', 2024, fetch=lambda u, **k: response(u, status), delay=0)
    with pytest.raises(requests.HTTPError):
        c.get(url)
    assert not c.paths(url)[0].exists() and not c.paths(url)[1].exists()


def test_registry_import_verified_snapshot_not_old_audit(tmp_path):
    url = 'https://icml.cc/a'
    source = tmp_path / 'legacy.html'
    source.write_bytes(b'old source')
    registry = tmp_path / 'registry.json'
    data = dict(venue='ICML', year=2025, status='VERIFIED', source_snapshots=[dict(
        url=url, snapshot=str(source), http_status=200, bytes=10, sha256=hashlib.sha256(b'old source').hexdigest())])
    write_json(registry, data)
    c = FetchCache(tmp_path / 'job', 'ICML', 2025, fetch=lambda *a, **k: pytest.fail('network'), delay=0)
    c.import_registry(registry, {url})
    assert c.get(url) == 'old source'
    assert not (c.base / 'raw/Audit_Summary.json').exists()


def test_bad_registry_hash_rejected(tmp_path):
    source = tmp_path / 'legacy'
    source.write_bytes(b'a')
    registry = tmp_path / 'registry.json'
    write_json(registry, dict(venue='ICML', year=2025, source_snapshots=[dict(url='https://icml.cc/a', snapshot=str(source), http_status=200, bytes=1, sha256='bad')]))
    with pytest.raises(ValueError, match='integrity'):
        FetchCache(tmp_path / 'job', 'ICML', 2025).import_registry(registry, {'https://icml.cc/a'})


@pytest.mark.parametrize('venue,years', [('ECCV', [2025]), ('TPAMI', [2024]), ('CVPR', [2025]), ('ICLR', [2024, 2024])])
def test_invalid_requests_no_io(tmp_path, venue, years):
    with pytest.raises(ValueError):
        runner.run_years(venue, years, tmp_path)
    assert not list(tmp_path.iterdir())


def test_tpami_explicit_not_implemented():
    with pytest.raises(ValueError, match='NOT_IMPLEMENTED'):
        runner.validate_request('TPAMI', [2025])


def test_protected_icml2024_has_no_io(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'PRODUCTION', tmp_path)
    assert runner.run_year('ICML', 2024, tmp_path)['status'] == 'PROTECTED'
    assert not list(tmp_path.iterdir())


def test_keyboard_interrupt_stops_requested_batch(tmp_path):
    visited = []
    def run(*args):
        visited.append(args[1])
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        runner.run_years('ICLR', [2024, 2025], tmp_path, runner=run)
    assert visited == [2024]


def test_title_variants_require_identity_and_duplicate_events_explained():
    a = row()
    b = {**a, 'Title': 'Updated published title', 'Program_Event_ID': 1}
    rows, count = reconcile([a], [b, b.copy()])
    assert count == 1
    assert {r['Status'] for r in rows} == {'TITLE_VARIANT', 'DUPLICATE_EVENT'}
    assert all(r['Resolved'] for r in rows)


def test_same_title_different_identities_not_collapsed():
    a = row()
    b = {**a, 'Official_URL': 'https://independent/2'}
    c = {**b, 'Official_URL': 'https://independent/3'}
    rows, count = reconcile([a], [b, c])
    assert count == 2 and any(r['Status'] == 'DUPLICATE_IDENTITY' for r in rows)


@pytest.mark.parametrize('field,value', [('Abstract', ''), ('Paper_ID', ''), ('Venue', 'ICML'), ('Authors', 'Alice et al.')])
def test_invalid_metadata_blocks_formal(tmp_path, field, value):
    a = row()
    result = audit(tmp_path, 'ICLR', 2025, [a], [{**a, field: value}], [a], evidence_complete=True)
    assert result['status'] == 'REVIEW_REQUIRED'
    assert not (tmp_path / 'raw/Formal_Proceedings_Corpus.csv').exists()


def test_no_runtime_dependencies_and_frozen_hash():
    root = Path(__file__).resolve().parents[1]
    for name in ['run_venue.py', 'venue_runtime.py', 'venue_audit.py', 'venue_pipelines.py', 'screen_verified.py']:
        text = (root / name).read_text(encoding='utf-8')
        assert all(x not in text for x in ['allvenues_runtime', 'work_handoff_allvenues', 'controller.lock', 'venue_queue.csv'])
    assert hashlib.sha256(RULES.read_bytes()).hexdigest() == RULES_SHA256


def test_partial_screening_is_not_published(tmp_path, monkeypatch):
    import pandas as pd
    a = row()
    base = tmp_path / 'ICLR/2025'
    audit(base, 'ICLR', 2025, [a], [a], [a], evidence_complete=True)
    monkeypatch.setattr(pd.DataFrame, 'to_csv', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        screen('ICLR', 2025, library_root=tmp_path, write=True)
    assert not (base / 'screening').exists()
    assert not (base / 'runtime/run.lock').exists()


def test_official_enumeration_parsers():
    html = '<a href="https://ojs.aaai.org/index.php/AAAI/issue/view/1">Vol. 39 No. 1: AAAI-25</a>'
    assert len(aaai_issue_links(html, 'https://aaai.org/', 2025)) == 1
    assert not aaai_issue_links(html, 'https://aaai.org/', 2024)
    ecva = '<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/1.html">Title</a></dt><dd><a>Alice</a><a>Bob</a></dd>'
    assert ecva_index(ecva, 2024)[0]['Authors'] == 'Alice; Bob'
    with pytest.raises(ValueError):
        ecva_index(ecva, 2026)


def test_oai_independent_identity_and_scope(tmp_path):
    xml = '''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <ListRecords><record><header/><metadata><dc:source>Proceedings of the AAAI Conference on Artificial Intelligence; Vol. 39 No. 1</dc:source>
    <dc:title>Test</dc:title><dc:creator>Alice</dc:creator><dc:identifier>https://ojs.aaai.org/index.php/AAAI/article/view/1</dc:identifier></metadata></record></ListRecords></OAI-PMH>'''
    c = FetchCache(tmp_path, 'AAAI', 2025, fetch=lambda u, **k: response(u, body=xml.encode()), delay=0)
    assert aaai_oai(c, 2025, {1})[0]['Title'] == 'Test'
    assert aaai_oai(c, 2024, {1}) == []


def test_explicit_refresh_preserves_details_and_source_history(tmp_path):
    index = 'https://proceedings.mlr.press/v267/'
    detail = index + 'x.html'
    calls = []
    def fetch(u, **k):
        calls.append(u)
        return response(u, body=str(len(calls)).encode())
    c = FetchCache(tmp_path, 'ICML', 2025, fetch=fetch, delay=0)
    assert c.get(index) == '1' and c.get(detail) == '2'
    c = FetchCache(tmp_path, 'ICML', 2025, fetch=fetch, delay=0, refresh_evidence=True)
    assert c.get(index) == '3' and c.get(index) == '3' and c.get(detail) == '2'
    assert len(calls) == 3
    assert len(list((c.directory / 'history').glob('*.body'))) == 1


def test_direct_screen_rejects_eccv2025_and_boolean_gate(tmp_path):
    with pytest.raises(ValueError):
        screen('ECCV', 2025, library_root=tmp_path)
    r = row()
    base = tmp_path / 'ICLR/2025'
    result = audit(base, 'ICLR', 2025, [r], [r], [r], evidence_complete=True)
    result['unresolved_count'] = False
    write_json(base / 'raw/Audit_Summary.json', result)
    with pytest.raises(ValueError, match='zero unresolved'):
        screen('ICLR', 2025, library_root=tmp_path)
