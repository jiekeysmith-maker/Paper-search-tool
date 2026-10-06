"""Conservative submission identity and author evidence, independent of event counts.

Only explicit OpenReview forum IDs are strong keys; empty/unknown URLs are not.
Original observations are retained so aggregation never erases conflicting evidence.
"""
from collections import Counter
from html import unescape
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit
import json


def decode_entities(value):
    value = str(value or '')
    # Some source JSON contains an HTML-escaped HTML entity. Bounded, repeatable.
    for _ in range(4):
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    return value


def event_placeholder(value):
    """Conference-generated event references are not OpenReview submissions."""
    p = urlsplit(str(value or ''))
    ids = parse_qs(p.query).get('id', [])
    return bool(p.hostname == 'openreview.net' and len(ids) == 1 and
                re.fullmatch(r'\d{4}-(?:Oral|Poster|Spotlight)--?\d+-[A-Za-z0-9]+', ids[0], re.I))


def forum_id(value):
    if event_placeholder(value):
        return ''
    p = urlsplit(unescape(str(value or '')).strip())
    if (p.scheme not in ('http', 'https') or p.hostname != 'openreview.net'
            or p.username or p.port not in (None, 80, 443) or p.path.rstrip('/') != '/forum'):
        return ''
    ids = parse_qs(p.query).get('id', [])
    return ids[0] if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]+', ids[0]) else ''


def stable_ids(row):
    return {x for key in ('Official_URL', 'OpenReview_URL')
            if (x := forum_id(row.get(key)))}


def identity_keys(row):
    if '_Identity_Keys' in row:
        return set(row['_Identity_Keys'])
    keys = set()
    for field in ('Official_URL', 'OpenReview_URL', 'DOI'):
        value = unescape(str(row.get(field) or '')).strip()
        if not value:
            continue
        identity = forum_id(value)
        if identity:
            keys.add('openreview:' + identity)
        elif field != 'OpenReview_URL' and 'openreview.net' not in value:
            keys.add(value.replace('http://', 'https://').rstrip('/'))
    return keys


def _names(value):
    value = unicodedata.normalize('NFKD', decode_entities(value)).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"(?<=[^\W\d_])['’](?=[^\W\d_])", '', value)
    # Semicolons mark authors and permit "family, given" inside each name.
    parts = value.split(';') if ';' in value else value.split(',')
    return [re.findall(r'[^\W\d_]+', p, re.UNICODE) for p in parts if p.strip()]


def _name_compatible(a, b):
    if Counter(a) == Counter(b):
        return True
    # Require at least one shared full name token; initials alone are insufficient.
    shared = Counter(a) & Counter(b)
    if not any(len(t) > 1 for t in shared):
        return False
    left, right = list((Counter(a) - shared).elements()), list((Counter(b) - shared).elements())
    if not left or not right:
        # Only omitted middle initials, never omitted full given/family names.
        return all(len(t) == 1 for t in left + right)
    if len(left) != len(right):
        return False
    for token in left:
        choices = [t for t in right if token == t or
                   (min(len(token), len(t)) == 1 and token[0] == t[0])]
        if len(choices) != 1:
            return False
        right.remove(choices[0])
    return True


def compatible_authors(left, right):
    a, b = _names(left), _names(right)
    if not a or len(a) != len(b) or any(not n for n in a + b):
        return False
    if Counter(tuple(n) for n in a) == Counter(tuple(n) for n in b):
        return True
    # Unique one-to-one correspondence, not surname overlap or fuzzy strings.
    choices = [[j for j, other in enumerate(b) if _name_compatible(n, other)] for n in a]
    return all(len(c) == 1 for c in choices) and len({c[0] for c in choices}) == len(a)


def identity_conflict(left, right):
    a, b = stable_ids(left), stable_ids(right)
    if len(a) > 1 or len(b) > 1 or (a and b and a != b):
        return 'Conflicting explicit OpenReview forum IDs'
    if a and b and left.get('Authors') and right.get('Authors'):
        # Shared forum identity outranks author-list coverage/version differences.
        # Disjoint named authors are contradictory evidence, not formatting.
        if not any(_shared_name(x, y) for x in _names(left['Authors']) for y in _names(right['Authors'])):
            return 'Shared forum ID but incompatible complete author evidence'
    return ''


def _shared_name(a, b):
    """Corroboration only under a shared forum ID, never title-only confirmation."""
    # Non-decomposing Latin variants; source forms are preserved in observations.
    fold = str.maketrans({'ø':'o', 'ł':'l', 'đ':'d', 'ı':'i'})
    a, b = [t.translate(fold) for t in a], [t.translate(fold) for t in b]
    if _name_compatible(a, b) or ''.join(a) == ''.join(b):
        return True
    shorter, longer = sorted((Counter(a), Counter(b)), key=lambda x: sum(x.values()))
    return sum(len(t)>1 for t in shorter) >= 2 and not (shorter - longer)


def group_events(rows):
    """Connected identity components; never connect records by title or authors."""
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        for key in identity_keys(row):
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
    event_index = {}
    for i, row in enumerate(rows):
        if row.get('Program_Event_ID') is not None:
            event_index.setdefault((row.get('Source_Group'), str(row['Program_Event_ID'])), []).append(i)
    for i, row in enumerate(rows):
        related = row.get('Related_Event_IDs', [])
        if isinstance(related, str):
            related = json.loads(related) if related else []
        if not isinstance(related, list):
            raise ValueError('Related event IDs must be a list')
        for event_id in related:
            matches = event_index.get((row.get('Source_Group'), str(event_id)), [])
            if len(matches) != 1:
                continue
            j = matches[0]
            other = rows[j]
            # Explicit source relation plus corroboration. Never title-only grouping.
            title_a = ' '.join(unescape(row['Title']).casefold().split())
            title_b = ' '.join(unescape(other['Title']).casefold().split())
            if (title_a == title_b and compatible_authors(row.get('Authors'), other.get('Authors'))
                    and not identity_conflict(row, other)):
                parents[root(i)] = root(j)
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append(row)
    unique, results = [], []
    for observations in groups.values():
        first = observations[0]
        # Non-event sources retain their previous duplicate/conflict semantics.
        if len(observations) > 1 and not all('Program_Event_ID' in r for r in observations):
            unique.extend(observations)
            results.append(dict(Status='DUPLICATE_IDENTITY', Resolved=False,
                                Title=first['Title'], Reason='Repeated non-event identity'))
            continue
        merged = dict(first)
        merged['_Identity_Keys'] = sorted(set().union(*(identity_keys(r) for r in observations)))
        merged['_Events'] = [dict(r) for r in observations]
        conflicts = {identity_conflict(a, b) for a in observations for b in observations}
        conflicts.discard('')
        formal = {r.get('Official_URL', '').replace('http://', 'https://').rstrip('/')
                  for r in observations if r.get('Official_URL') and not forum_id(r['Official_URL'])}
        if len(formal) > 1:
            conflicts.add('One submission claims multiple publisher URLs')
        if conflicts:
            merged['_Identity_Conflict'] = '; '.join(sorted(conflicts))
            results.append(dict(Status='DUPLICATE_IDENTITY', Resolved=False,
                                Title=first['Title'], Reason=merged['_Identity_Conflict']))
        # Retain stable ID even if the first event omits it.
        review = next((r.get('OpenReview_URL') for r in observations if forum_id(r.get('OpenReview_URL'))), '')
        if review:
            merged['OpenReview_URL'] = review
        for extra in observations[1:]:
            results.append(dict(Status='DUPLICATE_EVENT', Resolved=not conflicts,
                                Title=extra['Title'], Program_Event_ID=extra['Program_Event_ID'],
                                Reason='Additional event for stable submission; observations retained'))
        unique.append(merged)
    return unique, results
