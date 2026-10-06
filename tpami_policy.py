"""Frozen corpus inclusion policy: final assigned issue year, never DOI/EA year."""
import csv
from pathlib import Path
import re
from datetime import datetime

YEAR_BASIS = 'TPAMI_FINAL_ISSUE_YEAR_V1'
TITLE = 'IEEE Transactions on Pattern Analysis and Machine Intelligence'


def normalize_date(value):
    """Preserve available precision; do not invent a day for month-only dates."""
    value=str(value or '').strip()
    for pattern in ('%Y-%m-%d','%Y/%m/%d','%d %B %Y','%d %b %Y','%B %d, %Y','%b %d, %Y'):
        try:return datetime.strptime(value,pattern).strftime('%Y-%m-%d')
        except ValueError:pass
    for pattern in ('%Y-%m','%Y/%m','%B %Y','%b %Y'):
        try:return datetime.strptime(value,pattern).strftime('%Y-%m')
        except ValueError:pass
    if re.fullmatch(r'\d{4}',value):return value
    return ''


def doi_key(value):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value).strip(), flags=re.I).casefold()


def admission(base):
    """Caller holds its own year lock; other TPAMI years must remain sequential.

    No shared mutable DOI registry and no global lock. With each caller creating
    its own lock before this check, overlapping TPAMI-year admissions fail closed.
    Other venues are never inspected.
    """
    for other in (2024, 2025, 2026):
        if str(other) != base.name and (base.parent / str(other) / 'runtime/run.lock').exists():
            raise RuntimeError(f'TPAMI requires sequential years; other year lock exists: {other}')


def corpus_issues(base, rows):
    issues, seen = [], set()
    other_dois = {}
    for other in (2024, 2025, 2026):
        if str(other) == base.name:
            continue
        path = base.parent / str(other) / 'raw/Formal_Proceedings_Corpus.csv'
        if path.is_file():
            with path.open(encoding='utf-8-sig', newline='') as f:
                for r in csv.DictReader(f):
                    other_dois[doi_key(r.get('DOI', ''))] = other
    for r in rows:
        doi = doi_key(r.get('DOI', ''))
        reason = None
        if not doi.startswith('10.1109/tpami.'):
            reason = 'Missing/non-TPAMI DOI'
        elif doi in seen or doi in other_dois:
            reason = f'Duplicate DOI within/across final years: {doi}; other={other_dois.get(doi)}'
        elif (r.get('Year_Basis') != YEAR_BASIS or str(r.get('Final_Issue_Year')) != str(r.get('Year'))
              or not str(r.get('Volume', '')).isdigit() or not str(r.get('Issue', '')).isdigit()
              or int(r['Volume'])<1 or int(r['Issue'])<1
              or not normalize_date(r.get('Issue_Publication_Date','')).startswith(str(r.get('Year')))):
            reason = 'Final issue year/volume/issue/date contract failed'
        elif r.get('Official_URL')!='https://ieeexplore.ieee.org/document/'+str(r.get('Native_Publisher_ID','')) or not str(r.get('Native_Publisher_ID','')).isdigit():
            reason='IEEE document identity/URL mismatch'
        else:
            from eccv_metadata import abstract_issue
            reason=abstract_issue(r.get('Title',''),r.get('Abstract','')) or None
        if reason:
            issues.append(dict(Status='DUPLICATE_IDENTITY' if 'Duplicate' in reason else 'OTHER_UNRESOLVED',
                               DOI=doi, Reason=reason))
        seen.add(doi)
    return issues
