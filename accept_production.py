"""Foreground acceptance only; output location cannot target the production library."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from run_venue import run_year, validate_request
from venue_runtime import FetchCache, write_json, utc
from screen_verified import verified_corpus

ROOT = Path(__file__).resolve().parent / 'output' / 'production_acceptance'
SEED_ROOT = ROOT
OFFLINE = False


def seed(cache):
    """Reuse validated snapshots, never copy a previous audit or mutable state."""
    venue, year = cache.venue, cache.year
    sources = [Path(__file__).resolve().parent / 'output' / 'smoke' / venue / str(year) / 'runtime/cache']
    if ROOT != SEED_ROOT:
        sources.append(SEED_ROOT / venue / str(year) / 'runtime/cache')
    if venue == 'AAAI':
        sources += [root / venue / str(y) / 'runtime/cache' for root in {ROOT, SEED_ROOT} for y in (2024, 2025, 2026) if y != year]
        sources.append(SEED_ROOT / venue / 'source_investigation/runtime/cache')
        sources += [p for p in (SEED_ROOT / 'attempts').glob(f'*/{venue}/{year}/runtime/cache') if p != cache.directory]
    if venue == 'TPAMI':
        sources += [ROOT / venue / str(y) / 'runtime/cache' for y in (2024, 2025, 2026) if y != year]
    entries = []
    for source in sources:
        for path in source.glob('*.json'):
            item = json.loads(path.read_text(encoding='utf-8'))
            url = item['url']
            same_year = item['year'] == year
            global_catalog = venue == 'AAAI' and ('/AAAI/oai?' in url or '/AAAI/issue/archive' in url)
            global_catalog |= venue == 'TPAMI' and (url.endswith('/robots.txt') or 'RecentIssue.jsp?punumber=34' in url)
            if not (same_year or global_catalog):
                continue
            body = path.with_suffix('.body')
            entries.append(dict(url=url, snapshot=str(body), sha256=item['sha256'], bytes=item['bytes'], http_status=item['http_status'], utc=item.get('utc')))
    if venue == 'TPAMI':
        for path in (ROOT / venue / 'source_investigation').glob('*.json'):
            item=json.loads(path.read_text(encoding='utf-8'))
            body=path.with_suffix('.body')
            if item.get('status')==200 and body.is_file() and item.get('final_url')==item['url']:
                entries.append(dict(url=item['url'],snapshot=str(body),sha256=item['sha256'],bytes=body.stat().st_size,http_status=200))
    registry = cache.base / 'runtime/Acceptance_Seed.json'
    write_json(registry, dict(venue=venue, year=year, source_snapshots=entries))
    cache.import_registry(registry, {e['url'] for e in entries})


def cache_factory(base, venue, year, **kwargs):
    if OFFLINE:
        def no_network(url, **unused):
            raise RuntimeError('OFFLINE_CACHE_MISS: '+url)
        kwargs['fetch']=no_network
    c = FetchCache(base, venue, year, **kwargs)
    seed(c)
    return c


def main():
    global ROOT, OFFLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venue', required=True, choices=['AAAI', 'ECCV', 'TPAMI'])
    parser.add_argument('--years', required=True, nargs='+', type=int)
    parser.add_argument('--attempt', help='New isolated acceptance attempt; preserves previous raw/reports')
    parser.add_argument('--offline', action='store_true', help='Replay hash-verified snapshots only; cache misses are errors')
    args = parser.parse_args()
    OFFLINE=args.offline
    if args.attempt:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',args.attempt):
            parser.error('attempt must be a simple directory name')
        ROOT = SEED_ROOT / 'attempts' / args.attempt
    validate_request(args.venue, args.years)
    for year in args.years:
        print(f'ACCEPTANCE {args.venue} {year}: START', flush=True)
        result = run_year(args.venue, year, ROOT, cache_factory=cache_factory)
        base = ROOT / args.venue / str(year)
        status = 'NOT_READY_FOR_PRODUCTION'
        if result['status'] == 'REVIEW_REQUIRED':
            status = 'REVIEW_REQUIRED'
        if result['status'] in ('SCREENED_CSV_READY', 'ALREADY_COMPLETE'):
            verified_corpus(base, args.venue, year)
            status = 'READY_FOR_PRODUCTION'
        record = dict(venue=args.venue, year=year, status=status, run=result, utc=utc())
        write_json(base / 'reports/Production_Readiness.json', record)
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
