"""Manual, single Venue-Year screening using the frozen V1.2 engine.

No network or writes on import. Validation-only by default; --write is explicit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
from src.screener import RuleEngine, build_audit_sample
from src.utils import (
    load_yaml, SCREENING_COLUMNS, DECISION_KEEP, DECISION_MAYBE,
    DECISION_AMBIGUOUS, DECISION_DROP,
)

RULES = Path(__file__).resolve().parent / 'config' / 'screening_rules_v1_2.yaml'
RULES_SHA256 = '4a94a8346cce3ab8dae5153ba9c07041371f783902d0bdb55becbc0d9c0cf514'
SUPPORTED_VENUES = ('ICML', 'ICLR', 'AAAI', 'ECCV')
MAPPING = dict(zip(
    ['Paper_ID', 'Title', 'Authors', 'Abstract', 'Venue', 'Year', 'Track', 'Official_URL', 'PDF_URL'],
    ['Paper_ID（论文编号）', 'Title（标题）', 'Authors（作者）', 'Abstract（摘要）',
     'Venue（会议/期刊）', 'Year（年份）', 'Track（论文轨道）',
     'Official_URL（官方论文页面）', 'PDF_URL（官方PDF链接）'],
))


def venue_year_path(library_root, venue, year):
    """Explicit root; no default production destination and no path traversal."""
    if venue not in SUPPORTED_VENUES or type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError('Unsupported Venue-Year (TPAMI is not implemented)')
    root = Path(library_root).resolve()
    base = root / venue / str(year)
    # Reject linked destinations that escape or merge Venue-Year directories.
    for path in (base, base / 'raw', base / 'screening'):
        if path.resolve() != path:
            raise ValueError('Linked Venue-Year paths are not supported')
    return base


def verified_corpus(base, venue, year):
    raw = base / 'raw'
    audit = json.loads((raw / 'Audit_Summary.json').read_text(encoding='utf-8-sig'))
    if audit.get('status') != 'VERIFIED':
        raise ValueError('Completeness gate did not pass')
    if audit.get('venue') != venue or str(audit.get('year')) != str(year):
        raise ValueError('Audit Venue-Year mismatch')
    if audit.get('unresolved_count') != 0:
        raise ValueError('Audit must explicitly report zero unresolved issues')
    corpus = raw / 'Formal_Proceedings_Corpus.csv'
    if corpus.resolve() != corpus:
        raise ValueError('Linked corpus is not supported')
    # Never follow an old absolute corpus_path into another environment.
    if audit.get('corpus_path') and audit['corpus_path'] != corpus.name:
        raise ValueError('corpus_path must be the local canonical filename or omitted')
    data = corpus.read_bytes()
    corpus_sha = sha256(data).hexdigest()
    if audit.get('corpus_sha256') != corpus_sha:
        raise ValueError('Missing corpus hash or corpus changed after audit')
    frame = pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False)
    required = set(MAPPING) | {'Formal_Publication_Evidence'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('Missing corpus columns or empty corpus')
    for column in required - {'PDF_URL'}:
        if not frame[column].str.strip().all():
            raise ValueError(f'Empty required field: {column}')
    if not frame.Paper_ID.is_unique or not frame.Official_URL.is_unique:
        raise ValueError('Duplicate corpus identity')
    if not frame.Venue.eq(venue).all() or not frame.Year.eq(str(year)).all():
        raise ValueError('Corpus Venue-Year mismatch')
    if type(audit.get('publisher_count')) is not int or len(frame) != audit['publisher_count']:
        raise ValueError('Publisher count mismatch')
    return frame, corpus_sha


def screen(venue, year, *, library_root, write=False):
    base = venue_year_path(library_root, venue, year)
    rule_sha = sha256(RULES.read_bytes()).hexdigest()
    if rule_sha != RULES_SHA256:
        raise ValueError('Frozen V1.2 rules hash mismatch')
    frame, corpus_sha = verified_corpus(base, venue, year)
    config = load_yaml(RULES)
    engine = RuleEngine(config)
    records = frame.to_dict('records')
    results = pd.DataFrame([
        engine.evaluate({label: row[key] for key, label in MAPPING.items()})
        for row in records
    ], columns=SCREENING_COLUMNS)
    tables = {'KD_Screening.csv': results}
    counts, candidates = {}, []
    by_id = {row['Paper_ID']: row for row in records}
    extra = ['Rule_Decision', 'Rule_Evidence', 'Rules_Version', 'Rules_SHA256',
             'Corpus_Audit_Status', 'Review_Status', 'Provisional_Review_Hint', 'Data_Issue']
    for decision in (DECISION_KEEP, DECISION_MAYBE, DECISION_AMBIGUOUS, DECISION_DROP):
        subset = results[results['Decision（筛选决定）'] == decision]
        key = decision.split('（')[0]
        counts[key] = len(subset)
        tables[f'{key}.csv'] = subset
        if decision != DECISION_DROP:
            for row in subset.to_dict('records'):
                candidates.append({
                    **by_id[row['Paper_ID（论文编号）']], 'Rule_Decision': key,
                    'Rule_Evidence': ' | '.join(row[c] for c in (
                        'Decision_Reason（筛选理由）', 'Positive_Hits（命中的阳性规则）',
                        'Negative_Hits（命中的负向规则）')),
                    'Rules_Version': 'V1.2', 'Rules_SHA256': rule_sha,
                    'Corpus_Audit_Status': 'VERIFIED',
                    'Review_Status': 'PENDING_HUMAN_SECONDARY_REVIEW',
                    'Provisional_Review_Hint': '', 'Data_Issue': '',
                })
    if sum(counts.values()) != len(frame):
        raise ValueError('Screening partition mismatch')
    tables['Needs_Secondary_Review.csv'] = pd.DataFrame(candidates, columns=list(frame.columns) + extra)
    drops = results[results['Decision（筛选决定）'] == DECISION_DROP].copy()
    drops['Rule_Score（规则分数）'] = pd.to_numeric(drops['Rule_Score（规则分数）'])
    sample = build_audit_sample(drops, config)
    tables['SAFE_DROP_Audit_Sample.csv'] = sample
    manifest = dict(
        utc=datetime.now(timezone.utc).isoformat(), venue=venue, year=year,
        status='SCREENED_CSV_READY' if write else 'VALIDATED_NO_WRITE',
        counts=counts, candidate_count=len(candidates), safe_drop_sample_count=len(sample),
        rules_sha256=rule_sha, corpus_sha256=corpus_sha,
        screening_entry_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        base_git_sha='8148669c8cdea1525efc7b89fa04f8f92ae57a69',
    )
    if write:
        out = base / 'screening'
        # New manual runs never silently overwrite a previous candidate pool.
        if out.exists():
            raise FileExistsError(f'Screening output already exists: {out}')
        out.mkdir(parents=True)
        for name, table in tables.items():
            table.to_csv(out / name, index=False, lineterminator='\n', encoding='utf-8-sig')
        (out / 'Run_Manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('venue', choices=SUPPORTED_VENUES)
    parser.add_argument('year', type=int)
    parser.add_argument('--library-root', type=Path, required=True)
    parser.add_argument('--write', action='store_true', help='Explicitly write this Venue-Year candidate pool')
    args = parser.parse_args()
    print(json.dumps(screen(args.venue, args.year, library_root=args.library_root, write=args.write), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
