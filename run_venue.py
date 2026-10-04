"""Manual foreground runner: one venue, explicitly ordered years, isolated jobs."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import traceback
import uuid

from screen_verified import screen, venue_year_path, verified_corpus, RULES, RULES_SHA256
from venue_runtime import FetchCache, job_lock, write_json, write_csv, utc
from venue_pipelines import Collection, PIPELINES
from venue_audit import audit

SUPPORTED = {'ICML': (2024, 2025, 2026), 'ICLR': (2024, 2025, 2026),
             'AAAI': (2024, 2025, 2026), 'ECCV': (2024, 2026)}
PRODUCTION = Path(r'D:\_Knowledge Distillation\Paper Library')


def validate_request(venue, years):
    if venue not in SUPPORTED:
        raise ValueError(f'{venue}: UNSUPPORTED / NOT_IMPLEMENTED by this runner')
    if not years or len(set(years)) != len(years) or any(type(y) is not int or y not in SUPPORTED[venue] for y in years):
        raise ValueError(f'Unsupported or duplicate years for {venue}; allowed: {SUPPORTED[venue]}')
    if sha256(RULES.read_bytes()).hexdigest() != RULES_SHA256:
        raise ValueError('Frozen V1.2 hash mismatch')


def completed_screening(base, venue, year):
    frame, corpus_hash = verified_corpus(base, venue, year)
    out = base / 'screening'
    m = json.loads((out / 'Run_Manifest.json').read_text(encoding='utf-8'))
    if (m.get('status') != 'SCREENED_CSV_READY' or m.get('venue') != venue or m.get('year') != year
            or m.get('corpus_sha256') != corpus_hash or m.get('rules_sha256') != RULES_SHA256):
        raise ValueError('Existing screening manifest does not match verified corpus/rules/scope')
    expected = {'KD_Screening.csv', 'RULE_KEEP.csv', 'RULE_MAYBE.csv', 'RULE_AMBIGUOUS.csv',
                'SAFE_DROP.csv', 'Needs_Secondary_Review.csv', 'SAFE_DROP_Audit_Sample.csv'}
    hashes = m.get('output_sha256', {})
    if set(hashes) != expected or any(sha256((out / name).read_bytes()).hexdigest() != digest for name, digest in hashes.items()):
        raise ValueError('Existing screening incomplete/unhashed/changed; preserved for manual inspection')
    return m


def run_year(venue, year, library_root, *, pipeline=None, cache_factory=FetchCache, refresh_evidence=False):
    validate_request(venue, [year])
    base = venue_year_path(library_root, venue, year)
    if venue == 'ICML' and year == 2024 and Path(library_root).resolve() == PRODUCTION.resolve():
        # No lock, logs or directory creation in this already finished production year.
        return dict(venue=venue, year=year, status='PROTECTED', reason='ICML2024 production is read-only; not rerun')
    run_id = utc().replace(':', '').replace('+', '_') + '-' + uuid.uuid4().hex[:8]
    # Lock acquisition failure must not write an error into the other process's job.
    with job_lock(base, venue, year):
        report = base / 'reports' / (run_id + '.json')
        collection = None
        cache = None
        result = dict(venue=venue, year=year, run_id=run_id, started=utc())
        try:
            for folder in ('raw', 'reports', 'logs'):
                (base / folder).mkdir(parents=True, exist_ok=True)
            write_json(base / 'runtime' / 'Progress.json', {**result, 'status': 'RUNNING'})
            formal = base / 'raw' / 'Formal_Proceedings_Corpus.csv'
            if (base / 'screening').exists():
                completed_screening(base, venue, year)
                result.update(status='ALREADY_COMPLETE')
            elif formal.exists():
                verified_corpus(base, venue, year)
                screen(venue, year, library_root=library_root, write=True, lock_held=True)
                result.update(status='SCREENED_CSV_READY')
            else:
                # A new attempt invalidates an earlier gate before collecting evidence.
                write_json(base / 'raw' / 'Audit_Summary.json', dict(venue=venue, year=year, status='IN_PROGRESS', unresolved_count=1))
                cache = cache_factory(base, venue, year, refresh_evidence=refresh_evidence)
                collection = Collection(cache)
                (pipeline or PIPELINES[venue])(cache, collection)
                sources = cache.manifest()
                write_json(base / 'raw' / 'Evidence_Manifest.json', dict(venue=venue, year=year, sources=sources))
                summary = audit(base, venue, year, collection.publisher, collection.corpus, collection.program,
                                evidence_complete=collection.evidence_complete, issues=collection.issues, excluded=collection.excluded)
                result.update(status=summary['status'], unresolved_count=summary['unresolved_count'])
                if summary['status'] == 'VERIFIED' and summary['unresolved_count'] == 0:
                    screen(venue, year, library_root=library_root, write=True, lock_held=True)
                    result['status'] = 'SCREENED_CSV_READY'
        except (Exception, KeyboardInterrupt) as exc:
            result.update(status='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED', error=repr(exc))
            write_json(base / 'logs' / (run_id + '-error.json'), dict(error=repr(exc), traceback=traceback.format_exc(), utc=utc()))
            if collection is not None and not (base / 'raw' / 'Formal_Proceedings_Corpus.csv').exists():
                collection.issue(repr(exc))
                collection.save()
                write_json(base / 'raw' / 'Audit_Summary.json', dict(venue=venue, year=year, status=result['status'], unresolved_count=max(1, len(collection.issues))))
            if isinstance(exc, KeyboardInterrupt):
                write_json(report, result)
                write_json(base / 'runtime' / 'Progress.json', result)
                raise
        finally:
            if cache:
                cache.manifest()
        result['finished'] = utc()
        write_json(report, result)
        write_json(base / 'runtime' / 'Progress.json', result)
        return result


def run_years(venue, years, library_root, *, runner=run_year, refresh_evidence=False):
    validate_request(venue, years)  # reject invalid entire request before any I/O
    results = []
    for year in years:  # preserve exactly the user-specified order, never sort or parallelize
        print(f'{venue} {year}: START', flush=True)
        try:
            result = runner(venue, year, library_root, refresh_evidence=True) if refresh_evidence else runner(venue, year, library_root)
        except Exception as exc:
            result = dict(venue=venue, year=year, status='FAILED', error=repr(exc))
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venue', required=True, type=str.upper)
    parser.add_argument('--years', required=True, type=int, nargs='+')
    parser.add_argument('--library-root', required=True, type=Path)
    parser.add_argument('--refresh-evidence', action='store_true',
                        help='Manually refresh discovery/index/program/OAI sources; retain cached paper details and old source evidence')
    args = parser.parse_args()
    try:
        results = run_years(args.venue, args.years, args.library_root, refresh_evidence=args.refresh_evidence)
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print('Stopped by user. Successful HTTP snapshots remain reusable; no background process.', file=sys.stderr)
        return 130
    return 0 if all(r['status'] in ('SCREENED_CSV_READY', 'ALREADY_COMPLETE', 'PROTECTED') for r in results) else 2


if __name__ == '__main__':
    raise SystemExit(main())
