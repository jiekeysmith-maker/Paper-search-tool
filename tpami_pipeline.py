"""Public final-issue enumeration; partial evidence survives source failures."""
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
import re
from venue_runtime import write_json
from tpami_adapter import TPAMIAdapter, NonResearchArticle, EarlyAccessRecord
from tpami_policy import YEAR_BASIS
from tpami_public_pages import csdl_annual_url
from eccv_access import ECCVAccess as OfficialHostAccess


def tpami(cache, collection):
    year, adapter = cache.year, TPAMIAdapter()
    sources = [('publisher', 'https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=34'),
               ('independent', csdl_annual_url(year))]
    inventories, statuses, permissions, enumeration, occurrences, events = {}, [], {}, [], [], []
    access=OfficialHostAccess(cache)

    def get_public(url):
        p=urlparse(url);host=p.netloc
        if '/api/' in p.path or p.path.startswith(('/rest','/ielx')):
            raise ValueError('Restricted endpoint forbidden')
        if host not in permissions:
            text=access.get(f'https://{host}/robots.txt')
            robots=RobotFileParser();robots.parse(text.splitlines())
            permissions[host]=(robots,text)
        robots,text=permissions[host]
        if not robots.can_fetch('PaperSearchTool',url) or (p.query and re.search(r'^Disallow:\s*/\*\?\*\s*$',text,re.M)):
            raise ValueError('ROBOTS_DISALLOWED: official source not requested')
        try:
            body=access.get(url)
            if re.search(r'<title[^>]*>\s*(?:Sign in|Log in|Authentication required)',body,re.I):
                raise ValueError('OFFICIAL_ACCESS_RESTRICTION: authentication page')
            return body
        except Exception as exc:
            if 'got 202' in str(exc) or 'OFFICIAL_ACCESS_RESTRICTION' in str(exc):
                access.blocked.setdefault(host,dict(url=url,error=repr(exc)))
                write_json(cache.base/'raw/Access_Restrictions.json',access.blocked)
            raise

    def save_evidence():
        write_json(cache.base/'raw/Publication_Events.json',events)
        write_json(cache.base/'raw/IEEE_Source_Enumeration.json',dict(
            year=year,year_basis=YEAR_BASIS,sources=statuses,inventories=inventories,
            issues=enumeration,publication_occurrences=occurrences,
            discovered_issue_count={k:len(v) for k,v in inventories.items()},
            enumerated_issue_count={role:sum(x['role']==role and x['complete'] for x in enumeration) for role,_ in sources},
            annual_article_count=len(collection.publisher) if collection.evidence_complete else None))
        collection.save()

    for role,url in sources:
        try:
            body=get_public(url)
            rows=adapter.volume_directory(body,url,year)
            inventories[role]=rows
            proof=adapter.inventory_evidence(body,rows)
            statuses.append(dict(role=role,url=url,status='ENUMERATED',**proof))
            if not proof['complete']:collection.issue(f'{role} issue inventory has no complete official count evidence')
        except Exception as exc:
            collection.issue(f'{role} annual source unavailable: {exc}')
            statuses.append(dict(role=role,url=url,status='UNAVAILABLE',error=repr(exc),complete=False))
        save_evidence()

    for role,contexts in inventories.items():
        target=collection.publisher if role=='publisher' else collection.program
        annual_seen={}
        for context in sorted(contexts,key=lambda r:(int(r['Volume'] or 0),int(r['Issue']))):
            queue=[context['Issue_URL']];seen=set();records={};counts=set();errors=[];total=0
            while queue:
                url=queue.pop(0)
                if url in seen:continue
                if len(seen)>=500:
                    errors.append('Pagination safety bound exceeded');break
                seen.add(url)
                try:
                    page=adapter.issue_page(get_public(url),url,context)
                    context.update(page.get('context',{}))
                    if page['declared_count'] is not None:counts.add(page['declared_count'])
                    for r in page['rows']:
                        total+=1;key=r['Native_Publisher_ID']
                        if key in records:
                            occurrences.append(dict(role=role,document_id=key,issue_url=context['Issue_URL'],page_url=url,kind='REPEATED_PAGINATION_OCCURRENCE'))
                            if records[key]!=r:errors.append('Conflicting repeated document '+key)
                        else:records[key]=r
                    queue.extend(page['pages'])
                except Exception as exc:
                    errors.append(repr(exc))
            if counts!={len(records)}:errors.append('Declared article total missing/conflicting or pagination incomplete')
            for key,r in records.items():
                if key in annual_seen:
                    collection.issue('Document assigned to multiple issues','DUPLICATE_IDENTITY',Native_Publisher_ID=key)
                else:
                    annual_seen[key]=r;target.append(r)
            enumeration.append(dict(role=role,issue_id=context['Issue_URL'],Volume=context['Volume'],Issue=context['Issue'],
                       pages=sorted(seen),declared_counts=sorted(counts),total_pre_dedup=total,
                       total_post_dedup=len(records),complete=not errors,errors=errors))
            for error in errors:collection.issue('Issue enumeration incomplete: '+error,Issue_URL=context['Issue_URL'])
            save_evidence()

    first={(r['Volume'],r['Issue']) for r in inventories.get('publisher',[])}
    second={(r['Volume'],r['Issue']) for r in inventories.get('independent',[])}
    if inventories and first!=second:collection.issue('Official final-year issue inventories disagree')
    excluded=set()
    for row in collection.publisher:
        try:
            detail=adapter.detail(get_public(row['Official_URL']),row['Official_URL'],row)
            collection.corpus.append(detail)
            events.append(dict(DOI=detail['DOI'],Native_Publisher_ID=detail['Native_Publisher_ID'],
                Event_Type='FINAL_ISSUE_ASSIGNMENT',Year=year,Volume=detail['Volume'],Issue=detail['Issue'],
                Issue_Publication_Date=detail['Issue_Publication_Date'],Early_Access_Date=detail['Early_Access_Date']))
            row.update({key:detail[key] for key in ('Title','Authors','DOI','Paper_ID')})
        except EarlyAccessRecord as exc:
            events.append({**exc.proof,'Event_Type':'EARLY_ACCESS_UNASSIGNED'})
            collection.excluded.append(exc.proof);excluded.add(row['Official_URL'])
            collection.issue('Unassigned Early Access event appeared in final issue enumeration',DOI=exc.proof['DOI'])
        except NonResearchArticle as exc:
            collection.excluded.append(exc.proof);excluded.add(row['Official_URL'])
        except Exception as exc:
            collection.issue('Document metadata unavailable/unresolved: '+repr(exc),Official_URL=row['Official_URL'])
        save_evidence()
    # Only the exact officially classified document may be excluded on both sides.
    collection.publisher=[r for r in collection.publisher if r['Official_URL'] not in excluded]
    collection.program=[r for r in collection.program if r['Official_URL'] not in excluded]
    collection.evidence_complete=bool(first) and first==second and not collection.issues and not access.blocked and all(s['complete'] for s in statuses)
    save_evidence()
