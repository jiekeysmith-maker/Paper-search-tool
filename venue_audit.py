"""Conservative independent reconciliation; differences never imply missing counts."""
from collections import Counter
from hashlib import sha256
from html import unescape
import re
import unicodedata

from screen_verified import MAPPING
from submission_identity import identity_keys, group_events, compatible_authors, identity_conflict
from venue_runtime import write_csv, write_json, utc

REQUIRED = set(MAPPING) | {'Formal_Publication_Evidence'}


def normalize(value):
    value = unicodedata.normalize('NFKC', unescape(str(value))).casefold()
    return re.sub(r'\s+', ' ', value.translate(str.maketrans({'–': '-', '—': '-', '’': "'", '“': '"', '”': '"'}))).strip()


def identities(row):
    return identity_keys(row)


def authors(value):
    # Publisher lists use commas; virtual-program lists use semicolons.
    # Preserve every name and its order; do not fuzzy-match initials/surnames.
    return normalize(re.sub(r'[,;]', ' ', str(value)))


def validate_rows(rows, venue, year):
    problems = []
    for row in rows:
        missing = sorted(k for k in REQUIRED - {'PDF_URL'} if not str(row.get(k, '')).strip())
        if missing or 'PDF_URL' not in row:
            problems.append(dict(Status='METADATA_INCOMPLETE', Paper_ID=row.get('Paper_ID', ''), Reason=','.join(missing or ['PDF_URL column'])))
        if row.get('Venue') != venue or str(row.get('Year')) != str(year):
            problems.append(dict(Status='OTHER_UNRESOLVED', Paper_ID=row.get('Paper_ID', ''), Reason='Venue/year mismatch'))
        if re.search(r'\bet al\.?\b|…|\.\.\.', row.get('Authors', ''), re.I):
            problems.append(dict(Status='METADATA_INCOMPLETE', Paper_ID=row.get('Paper_ID', ''), Reason='Truncated author list'))
    for key in ('Paper_ID', 'Official_URL'):
        for value, count in Counter(r.get(key, '') for r in rows).items():
            if count > 1:
                problems.append(dict(Status='DUPLICATE_IDENTITY', Reason=f'{key}: {value}; count={count}'))
    return problems


def reconcile(publisher, program):
    unique, results = group_events(program)
    used = set()
    identity_index, title_index = {}, {}
    for i, other in enumerate(unique):
        for identity in identities(other):
            identity_index.setdefault(identity, set()).add(i)
        observed_titles = {normalize(r['Title']) for r in other.get('_Events', [other])}
        for title in observed_titles:
            title_index.setdefault(title, []).append(i)
    title_counts = Counter(normalize(r['Title']) for r in publisher)
    publisher_keys = Counter(k for r in publisher for k in identities(r))
    for row in publisher:
        exact_ids = sorted({i for key in identities(row) for i in identity_index.get(key, ())})
        candidates = exact_ids or title_index.get(normalize(row['Title']), [])
        status, resolved, reason = 'INDEX_ONLY', False, 'No independent counterpart'
        other = {}
        if any(publisher_keys[k] > 1 for k in identities(row)):
            status, reason = 'DUPLICATE_IDENTITY', 'Multiple publisher records share a stable identity'
        elif len(candidates) > 1 or (not exact_ids and title_counts[normalize(row['Title'])] > 1):
            status, reason = 'DUPLICATE_IDENTITY', 'Ambiguous match; cannot collapse by title'
        elif len(candidates) == 1:
            i = candidates[0]
            other = unique[i]
            if not exact_ids:
                observed = [r for r in other.get('_Events', [other])
                            if normalize(r['Title']) == normalize(row['Title'])
                            and compatible_authors(row.get('Authors'), r.get('Authors'))]
                if observed:
                    other = {**other, 'Title': observed[0]['Title'], 'Authors': observed[0]['Authors']}
            if i in used:
                status, reason = 'DUPLICATE_IDENTITY', 'Independent record matched more than once'
            else:
                used.add(i)
                if row['Title'] == other['Title']:
                    status = 'EXACT'
                elif normalize(row['Title']) == normalize(other['Title']):
                    status = 'NORMALIZED_MATCH'
                else:
                    status = 'TITLE_VARIANT'
                if exact_ids and any(normalize(event['Title']) != normalize(row['Title'])
                                     for event in other.get('_Events', [])):
                    status = 'TITLE_VARIANT'
                resolved = True
                reason = 'Stable identity' if exact_ids else 'Unique title in both enumerations'
                if exact_ids and not compatible_authors(row.get('Authors', ''), other.get('Authors', '')):
                    reason += '; author representation/coverage differs; original observations retained'
                # Title-only matches require corroborating complete author metadata.
                conflict = identity_conflict(row, other)
                if conflict or other.get('_Identity_Conflict'):
                    status, resolved, reason = 'OTHER_UNRESOLVED', False, conflict or other['_Identity_Conflict']
                elif not exact_ids and not compatible_authors(row.get('Authors', ''), other.get('Authors', '')):
                    status, resolved, reason = 'OTHER_UNRESOLVED', False, 'Title-only match without matching authors'
        results.append(dict(Status=status, Resolved=resolved, Paper_ID=row.get('Paper_ID', ''),
                            Title=row['Title'], Independent_Title=other.get('Title', ''),
                            Publisher_Authors=row.get('Authors', ''),
                            Official_URL=row.get('Official_URL', ''),
                            Independent_URL=other.get('Official_URL', ''), Reason=reason,
                            Program_Events=other.get('_Events', [])))
    for i, row in enumerate(unique):
        if i not in used:
            results.append(dict(Status='PROGRAM_ONLY', Resolved=False, Title=row['Title'],
                                Independent_URL=row.get('Official_URL', ''),
                                OpenReview_URL=row.get('OpenReview_URL', ''), Program_Events=row.get('_Events', []),
                                Reason='No unique publisher counterpart'))
    return results, len(unique)


def audit(base, venue, year, publisher, corpus, program, *, evidence_complete, issues=(), excluded=()):
    raw = base / 'raw'
    if venue == 'ECCV':
        from eccv_identity import reconcile_eccv
        comparisons, independent_count = reconcile_eccv(publisher, program)
    else:
        comparisons, independent_count = reconcile(publisher, program)
    extra = list(issues) + validate_rows(corpus, venue, year)
    eccv_summary={}
    if venue == 'ECCV':
        from eccv_metadata import check_corpus
        metadata_issues,eccv_summary=check_corpus(corpus,publisher)
        extra.extend(metadata_issues)
    if venue == 'TPAMI':
        from tpami_policy import corpus_issues
        extra.extend(corpus_issues(base, corpus))
    for row in program:
        if not str(row.get('Title', '')).strip():
            extra.append(dict(Status='METADATA_INCOMPLETE', Reason='Independent record has no title',
                              Independent_URL=row.get('Official_URL', '')))
    if not publisher or not program or not evidence_complete:
        extra.append(dict(Status='OTHER_UNRESOLVED', Reason='Independent enumeration is empty or not established complete'))
    if Counter(r.get('Official_URL') for r in publisher) != Counter(r.get('Official_URL') for r in corpus):
        extra.append(dict(Status='METADATA_INCOMPLETE', Reason='Publisher/detail identity sets differ'))
    for row in extra:
        comparisons.append({**row, 'Resolved': False})
    comparisons.extend({**row, 'Status': 'NON_TARGET_TRACK', 'Resolved': True} for row in excluded)
    opened = [row for row in comparisons if not row['Resolved']]
    write_csv(raw / 'Publisher_Index.csv', publisher)
    write_csv(raw / 'Official_Program.csv', program)
    write_csv(raw / 'PROVISIONAL_Formal_Proceedings_Corpus.csv', corpus, list(dict.fromkeys([*MAPPING, 'Formal_Publication_Evidence', *(k for r in corpus for k in r)])))
    write_csv(raw / 'Corpus_Completeness_Audit.csv', comparisons)
    write_csv(raw / 'Open_Issues.csv', opened)
    result = dict(venue=venue, year=year, status='REVIEW_REQUIRED' if opened else 'VERIFIED',
                  unresolved_count=len(opened), publisher_count=len(publisher), metadata_count=len(corpus),
                  independent_unique_count=independent_count, utc=utc(),
                  counts_by_class=dict(Counter(r['Status'] for r in comparisons)),
                  difference_rows_are_not_missing_paper_counts=True)
    result.update(eccv_summary)
    if not opened:
        formal = raw / 'Formal_Proceedings_Corpus.csv'
        if formal.exists():
            raise FileExistsError(f'Refuse replacing formal corpus: {formal}')
        # Caller owns the job lock. Publish corpus first, audit gate last.
        from venue_runtime import atomic_bytes
        atomic_bytes(formal, (raw / 'PROVISIONAL_Formal_Proceedings_Corpus.csv').read_bytes())
        result['corpus_sha256'] = sha256(formal.read_bytes()).hexdigest()
        result['corpus_path'] = formal.name
    write_json(raw / 'Audit_Summary.json', result)
    return result
