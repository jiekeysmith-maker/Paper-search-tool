"""ECCV-only conservative, non-greedy cross-source identity evidence.

No author aliases, paper IDs, annual counts or semantic/LLM decisions.
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache
import html
import re
import unicodedata


def fold(text):
    text=str(text)
    for _ in range(3):
        decoded=html.unescape(text)
        if decoded==text:break
        text=decoded
    text=text.casefold().translate(str.maketrans({'ı':'i','ł':'l','–':'-','—':'-'}))
    return ''.join(c for c in unicodedata.normalize('NFKD',unicodedata.normalize('NFKC',text)) if not unicodedata.combining(c))


@lru_cache(maxsize=16384)
def title_tokens(text):
    text=fold(text)
    for command,symbol in [('epsilon','ε'),('alpha','α'),('beta','β'),('gamma','γ'),('delta','δ'),('lambda','λ')]:
        text=text.replace('\\'+command,' '+command+' ').replace(symbol,' '+command+' ')
    text=re.sub(r'\\(?:mathrm|mathbf|mathcal|text|operatorname)\b','',text)
    text=text.replace('+',' plus ').replace('-', '')
    return tuple(re.findall(r'[^\W_]+',text,flags=re.UNICODE))


def title_key(text):return ''.join(title_tokens(text))


def variant_safe(left,right):
    """Do not fuzzy-collapse different numbered/mathematical variants."""
    def signature(title):
        tokens=title_tokens(title)
        return (Counter(re.findall(r'\d+',fold(title))),
                Counter(t for t in tokens if t in {'plus','epsilon','alpha','beta','gamma','delta','lambda'}))
    return signature(left)==signature(right)


@lru_cache(maxsize=16384)
def people(text):
    if not isinstance(text,str) or not text.strip() or text.casefold().strip() in ('none','unknown','n/a'):return ()
    result=[]
    for person in str(text).split(';'):
        person=fold(person).strip().rstrip('*')
        if ',' in person:
            parts=person.split(',')
            if len(parts)!=2:return ()
            person=parts[1]+' '+parts[0]
        tokens=tuple(re.findall(r'[^\W\d_]+',person))
        if not tokens or tokens in (('et','al'),):return ()
        result.append(tokens)
    return tuple(result)


def person_key(name):return tuple(sorted(name))


def compatible(a,b):
    if person_key(a)==person_key(b):return True
    # Initial expansion needs an exact family name and compatible given names.
    # Full, conflicting given names are never fuzzily equated.
    if len(a)<2 or len(b)<2 or a[-1]!=b[-1]:return False
    def token(x,y):return x==y or (x[0]==y[0] and min(len(x),len(y))==1)
    if not token(a[0],b[0]):return False
    am,bm=a[1:-1],b[1:-1]
    return not am or not bm or (len(am)==len(bm) and all(token(x,y) for x,y in zip(am,bm)))


@lru_cache(maxsize=32768)
def author_evidence(left,right):
    a,b=people(left),people(right)
    ca,cb=Counter(map(person_key,a)),Counter(map(person_key,b))
    if not a or not b or any(n>1 for n in (*ca.values(),*cb.values())):
        return 'AUTHOR_CONFLICT'
    if a==b:return 'AUTHOR_EXACT'
    if ca==cb:return 'AUTHOR_SET_EXACT'
    edges={i:[j for j,y in enumerate(b) if compatible(x,y)] for i,x in enumerate(a)}
    solutions=0
    order=sorted(edges,key=lambda i:len(edges[i]))
    def visit(pos,used):
        nonlocal solutions
        if solutions>1:return
        if pos==len(order):solutions+=1;return
        for j in edges[order[pos]]:
            if j not in used:visit(pos+1,used|{j})
    if len(a)==len(b):visit(0,set())
    if solutions==1:return 'AUTHOR_INITIAL_COMPATIBLE'
    if solutions>1:return 'AUTHOR_CONFLICT'
    overlap=sum((ca&cb).values())/max(len(a),len(b))
    return 'AUTHOR_HIGH_OVERLAP' if overlap>=0.75 else 'AUTHOR_CONFLICT'


def stable_keys(row):
    keys=set()
    doi=str(row.get('DOI','')).strip().casefold()
    doi=re.sub(r'^https?://(?:dx\.)?doi.org/','',doi)
    if doi:keys.add('doi:'+doi)
    url=str(row.get('Official_URL','')).strip().rstrip('/')
    if url:keys.add('url:'+url)
    return keys


def reconcile_eccv(publisher,program):
    # Retain conflicting duplicates. Collapse only explicit identical events.
    records=[];results=[];seen={}
    for row in program:
        key=row.get('Official_URL','')
        old=seen.get(key)
        if old is not None and key:
            prior=records[old]
            if (row.get('Program_Event_ID') and row.get('Program_Event_ID')==prior.get('Program_Event_ID')
                    and row['Title']==prior['Title'] and row.get('Authors')==prior.get('Authors')):
                results.append(dict(Status='DUPLICATE_EVENT',Resolved=True,Title=row['Title'],Independent_URL=key,Reason='Identical official event occurrence'))
                continue
            results.append(dict(Status='DUPLICATE_IDENTITY',Resolved=False,Title=row['Title'],Independent_URL=key,Reason='Repeated independent identity'))
        seen[key]=len(records);records.append(row)
    titles=defaultdict(set);names=defaultdict(set);ids=defaultdict(set)
    for j,row in enumerate(records):
        titles[title_key(row['Title'])].add(j)
        for name in people(row.get('Authors','')):names[person_key(name)].add(j)
        for key in stable_keys(row):ids[key].add(j)
    edges={};by_left=defaultdict(list);by_right=defaultdict(list)
    strong={'AUTHOR_EXACT','AUTHOR_SET_EXACT','AUTHOR_INITIAL_COMPATIBLE'}
    for i,row in enumerate(publisher):
        key=title_key(row['Title']);candidates=set(titles.get(key,()))
        for name in people(row.get('Authors','')):candidates.update(names.get(person_key(name),()))
        for identity in stable_keys(row):candidates.update(ids.get(identity,()))
        for j in candidates:
            other=records[j];other_key=title_key(other['Title'])
            score=SequenceMatcher(None,key,other_key,autojunk=False).ratio()
            left,right=set(title_tokens(row['Title'])),set(title_tokens(other['Title']))
            overlap=len(left&right)/max(1,len(left|right))
            author=author_evidence(row.get('Authors',''),other.get('Authors',''))
            stable=bool(stable_keys(row)&stable_keys(other))
            exact=key==other_key and bool(key)
            # Word reordering or moderate editing needs a complete exact team,
            # at least three authors, and substantial lexical agreement.
            full_team=author in ('AUTHOR_EXACT','AUTHOR_SET_EXACT') and len(people(row.get('Authors','')))>=3
            explicit=(other.get('Publication_Link_Type')=='ECVA_EXPLICIT_DOI'
                      and other.get('Publication_Link_Evidence')==other.get('Official_URL')
                      and other.get('Publication_Link')==row.get('Official_URL') and stable)
            safe_variant=variant_safe(row['Title'],other['Title'])
            eligible=(stable and author in strong) or (explicit and author=='AUTHOR_HIGH_OVERLAP') or (author in strong and (exact or (safe_variant and score>=0.92))) or (safe_variant and full_team and overlap>=0.6)
            strength=1.1 if stable else 1.0 if exact else max(score,overlap)
            # Keep weak alternatives as competition/evidence; never greedy match.
            if exact or stable or score>=0.65 or author in strong or author=='AUTHOR_HIGH_OVERLAP':
                edge=dict(Author_Evidence=author,Title_Similarity=round(score,6),Title_Token_Overlap=round(overlap,6),
                          Identity_Evidence='ECVA_EXPLICIT_DOI' if explicit else 'SHARED_OFFICIAL_ID' if stable else 'NORMALIZED_TITLE' if exact else 'TITLE_AND_AUTHORS',
                          Publication_Link_Evidence=other.get('Publication_Link_Evidence',''),
                          eligible=eligible,strength=strength)
                edges[i,j]=edge;by_left[i].append((strength,j));by_right[j].append((strength,i))
    def best(options):
        ranked=sorted(options,reverse=True)
        return ranked[0][1] if ranked and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=0.04) else None
    matched=set()
    for i,row in enumerate(publisher):
        j=best(by_left[i]);edge=edges.get((i,j))
        resolved=bool(edge and edge['eligible'] and best(by_right[j])==i)
        # For unresolved rows retain the best candidate and competing identities.
        candidate=j if j is not None else (max(by_left[i])[1] if by_left[i] else None)
        evidence=edges.get((i,candidate),{})
        other=records[candidate] if candidate is not None else {}
        status='PUBLISHER_ONLY_UNRESOLVED'
        if resolved:
            matched.add(j)
            status='EXACT' if row['Title']==other['Title'] and evidence['Author_Evidence']=='AUTHOR_EXACT' else 'NORMALIZED_MATCH' if title_key(row['Title'])==title_key(other['Title']) and evidence['Author_Evidence']=='AUTHOR_EXACT' else 'AUTHOR_VARIANT_MATCH' if title_key(row['Title'])==title_key(other['Title']) else 'TITLE_VARIANT_MATCH'
        elif evidence:
            status='AUTHOR_CONFLICT' if evidence['Author_Evidence']=='AUTHOR_CONFLICT' else 'AUTHOR_REVIEW_REQUIRED' if evidence['Author_Evidence']=='AUTHOR_HIGH_OVERLAP' else 'IDENTITY_REVIEW_REQUIRED'
        results.append(dict(Status=status,Resolved=resolved,Identity_Status='AUTO_CONFIRMED' if resolved else 'CONFLICT' if status=='AUTHOR_CONFLICT' else 'REVIEW_CANDIDATE',Paper_ID=row.get('Paper_ID',''),Title=row['Title'],
            Official_URL=row.get('Official_URL',''),Independent_Title=other.get('Title',''),Independent_URL=other.get('Official_URL',''),
            Publisher_Authors=row.get('Authors',''),Independent_Authors=other.get('Authors',''),
            **{k:v for k,v in evidence.items() if k not in ('eligible','strength')},
            Competing_Independent_URLs='; '.join(records[k]['Official_URL'] for _,k in sorted(by_left[i],reverse=True)[:5]),
            Reason='Unique bidirectional multi-evidence match with competition margin' if resolved else 'No unambiguous sufficient identity proof; candidates are not confirmed matches'))
    for j,row in enumerate(records):
        if j not in matched:results.append(dict(Status='ECVA_ONLY_UNRESOLVED',Resolved=False,Title=row['Title'],Independent_URL=row.get('Official_URL',''),Reason='No confirmed publisher counterpart; not a missing-paper count'))
    independent_unique=len({('url',r['Official_URL']) if r.get('Official_URL') else ('unidentified',i)
                            for i,r in enumerate(records)})
    return results,independent_unique
