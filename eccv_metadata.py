"""Additional ECCV publication/abstract checks; never authorizes screening alone."""
from collections import Counter
import re
from eccv_identity import fold


def abstract_issue(title,abstract):
    value=' '.join(fold(abstract).split()).strip(' .:')
    if not value or not any(c.isalpha() for c in value):return 'Missing abstract'
    if value==' '.join(fold(title).split()).strip(' .:'):return 'Abstract repeats title'
    if value in {'abstract','n/a','na','none','null','not available','abstract unavailable','no abstract available',
                 'abstract not available','no abstract is available','loading','loading abstract','table of contents','access denied',
                 'download pdf','view chapter','buy this chapter','about this book'}:
        return 'Placeholder/navigation instead of abstract'
    if len(value.split())<35 and re.search(r'please (?:log in|sign in)|enable javascript|accept (?:all )?cookies|verify you are human',value):
        return 'Access/navigation text instead of abstract'
    return ''


def check_corpus(rows,publisher=None):
    issues=[];valid_abstracts=0;duplicate_records=set()
    for r in rows:
        reason=abstract_issue(r.get('Title',''),r.get('Abstract',''))
        if reason:issues.append(dict(Status='METADATA_INCOMPLETE',Paper_ID=r.get('Paper_ID',''),Reason=reason))
        else:valid_abstracts+=1
        doi=str(r.get('DOI','')).strip()
        if not doi.startswith('10.1007/') or r.get('Official_URL')!='https://link.springer.com/chapter/'+doi:
            issues.append(dict(Status='OTHER_UNRESOLVED',Paper_ID=r.get('Paper_ID',''),Reason='Springer DOI/Official_URL identity mismatch'))
    for key in ('Paper_ID','Official_URL','DOI'):
        counts=Counter(r.get(key,'') for r in rows)
        repeated={v for v,n in counts.items() if v and n>1}
        for i,r in enumerate(rows):
            if r.get(key) in repeated:duplicate_records.add(i)
        for value in repeated:issues.append(dict(Status='DUPLICATE_IDENTITY',Reason=f'Duplicate ECCV {key}: {value}'))
    expected=len(publisher) if publisher is not None else len(rows)
    missing=sum(not str(r.get('Abstract','')).strip() for r in rows)+max(0,expected-len(rows))
    summary=dict(formal_count=expected,title_present_count=sum(bool(str(r.get('Title','')).strip()) for r in rows),
                 abstract_present_count=valid_abstracts,abstract_missing_count=missing,
                 abstract_invalid_count=len(rows)-valid_abstracts-sum(not str(r.get('Abstract','')).strip() for r in rows),
                 duplicate_identity_count=len(duplicate_records),formal_count_basis='target publisher rows; not VERIFIED authorization')
    return issues,summary
