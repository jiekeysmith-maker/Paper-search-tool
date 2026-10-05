"""Serial official-source enumerators. No scheduler, implicit year, or PDF fetch."""
import json
import re
from urllib.parse import urljoin, urlencode
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import yaml

from venue_adapters import PMLRAdapter, independent_program
from iclr_adapter import ICLRAdapter
from ojs_adapter import AAAIOJSAdapter
from springer_adapter import SpringerECCVAdapter
from venue_runtime import write_csv, write_json, utc

VOLUMES = {2024: 235, 2025: 267, 2026: 306}


class Collection:
    def __init__(self, cache):
        self.cache = cache
        self.publisher, self.corpus, self.program, self.issues, self.excluded = [], [], [], [], []
        self.evidence_complete = False

    def issue(self, reason, status='OTHER_UNRESOLVED', **values):
        self.issues.append(dict(Status=status, Reason=reason, **values))

    def save(self):
        raw = self.cache.base / 'raw'
        for name, rows in [('Publisher_Index', self.publisher), ('Official_Program', self.program),
                           ('PROVISIONAL_Formal_Proceedings_Corpus', self.corpus), ('Open_Issues', self.issues),
                           ('Non_Target_Records', self.excluded)]:
            write_csv(raw / (name + '.csv'), rows)
        write_json(self.cache.base / 'runtime' / 'Progress.json', dict(
            venue=self.cache.venue, year=self.cache.year, status='COLLECTING', utc=utc(),
            publisher_count=len(self.publisher), metadata_count=len(self.corpus),
            issue_count=len(self.issues), requests=self.cache.requests, cache_hits=self.cache.hits))

    def details(self, parser, rows=None):
        rows = self.publisher if rows is None else rows
        self.save()
        try:
            for number, row in enumerate(rows, 1):
                html = self.cache.get(row['Official_URL'])
                try:
                    self.corpus.append(parser(html, row))
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    self.issue(str(exc), 'METADATA_INCOMPLETE', Official_URL=row['Official_URL'])
                if number % 25 == 0 or number == len(rows):
                    print(f'{self.cache.venue} {self.cache.year}: details {number}/{len(rows)}; cache hits={self.cache.hits}', flush=True)
                    self.save()
        finally:
            self.save()


def conference_program(cache, venue, year):
    url = f'https://{venue.lower()}.cc/static/virtual/data/{venue.lower()}-{year}-orals-posters.json'
    data = json.loads(cache.get(url))
    target, excluded = independent_program(data, venue, year)
    result = []
    for row in target:
        virtual = row.get('virtualsite_url', '')
        if not virtual.startswith(f'/virtual/{year}/'):
            raise ValueError('Program event year missing/mismatched')
        links = [row.get('paper_url'), *(m.get('uri') for m in row.get('eventmedia', []))]
        links = [x for x in links if isinstance(x, str)]
        formal = next((x for x in links if x.startswith(('https://proceedings.mlr.press/', 'https://proceedings.iclr.cc/'))), '')
        review = next((x for x in links if x.startswith('https://openreview.net/forum?')), '')
        result.append(dict(Title=row['name'], Authors='; '.join(a['fullname'] for a in row['authors']),
                           Official_URL=formal, OpenReview_URL=review,
                           Evidence_URL=urljoin(f'https://{venue.lower()}.cc', virtual),
                           Program_Event_ID=row['id'], Source_Group=row['sourceurl']))
    return result, [dict(Title=r.get('name', ''), Reason='Outside accepted target conference groups',
                         Source_Group=r.get('sourceurl', '')) for r in excluded]


def icml(cache, collection):
    year = cache.year
    volume = VOLUMES[year]
    url = f'https://proceedings.mlr.press/v{volume}/'
    metadata = url + 'assets/bib/citeproc.yaml'
    program = f'https://icml.cc/static/virtual/data/icml-{year}-orals-posters.json'
    cache.import_registry(cache.base / 'raw' / 'Source_Registry.json', {url, metadata, program})
    adapter = PMLRAdapter()
    collection.publisher = adapter.index(cache.get(url), url, year)
    collection.save()
    collection.program, collection.excluded = conference_program(cache, 'ICML', year)
    rows = yaml.safe_load(cache.get(metadata))
    if not isinstance(rows, list):
        raise ValueError('PMLR citeproc must be a list')
    by_url = {}
    for row in rows:
        normalized = adapter.normalize(row, year, volume, metadata, utc())
        if normalized['Official_URL'] in by_url:
            collection.issue('Duplicate citeproc identity', 'DUPLICATE_IDENTITY', Official_URL=normalized['Official_URL'])
        by_url[normalized['Official_URL']] = normalized
    publisher_urls = {r['Official_URL'] for r in collection.publisher}
    for url in by_url.keys() - publisher_urls:
        collection.issue('Citeproc record absent from publisher index', 'OTHER_UNRESOLVED', Official_URL=url)
    missing = []
    for row in collection.publisher:
        normalized = by_url.get(row['Official_URL'])
        if normalized and normalized.get('Abstract') and normalized.get('Authors'):
            normalized['OpenReview_URL'] = row.get('OpenReview_URL', '')
            collection.corpus.append(normalized)
        else:
            missing.append(row)
    def detail(html, row):
        d = adapter.detail(html, row['Official_URL'], year, volume)
        soup = BeautifulSoup(html, 'html.parser')
        pdf = soup.select_one('meta[name="citation_pdf_url"]')
        return dict(Paper_ID=f'ICML{year}_PMLR_' + row['Official_URL'].rsplit('/', 1)[-1].removesuffix('.html'),
                    Title=d['title'], Abstract=d['abstract'], Authors='; '.join(d['authors']),
                    Venue='ICML', Year=year, Track='Main Conference', Official_URL=row['Official_URL'],
                    PDF_URL=pdf['content'] if pdf else '', OpenReview_URL=row.get('OpenReview_URL', ''),
                    Formal_Publication_Evidence=d['bibtex'], Metadata_Source=row['Official_URL'])
    collection.details(detail, missing)
    collection.evidence_complete = True


def iclr(cache, collection):
    year = cache.year
    url = f'https://proceedings.iclr.cc/paper_files/paper/{year}'
    adapter = ICLRAdapter()
    collection.publisher, collection.excluded = adapter.index(cache.get(url), url, year)
    collection.save()
    collection.program, excluded = conference_program(cache, 'ICLR', year)
    collection.excluded.extend(excluded)
    def detail(html, row):
        result = adapter.detail(html, row['Official_URL'], year, utc())
        soup = BeautifulSoup(html, 'html.parser')
        result['OpenReview_URL'] = next((a['href'] for a in soup.select('a[href]') if a['href'].startswith('https://openreview.net/forum?')), '')
        row['OpenReview_URL'] = result['OpenReview_URL']
        return result
    collection.details(detail)
    collection.evidence_complete = True


def aaai_issue_links(html, url, year):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for a in soup.select('a[href]'):
        link = urljoin(url, a['href']).rstrip('/')
        text = a.get_text(' ', strip=True)
        if re.fullmatch(r'https://ojs.aaai.org/index.php/AAAI/issue/view/\d+', link) and re.search(rf'AAAI-{str(year)[-2:]}\b', text):
            found[link] = text
    return found


def aaai_oai(cache, year, target_issues):
    from aaai_pipeline import enumerate_oai
    return enumerate_oai(cache, year, target_issues)


def aaai(cache, collection):
    year, adapter = cache.year, AAAIOJSAdapter()
    mirror = f'https://aaai.org/proceeding/aaai-{year - 1986}-{year}/'
    independent_issues = aaai_issue_links(cache.get(mirror), mirror, year)
    if not independent_issues:
        raise ValueError('AAAI official annual issue directory absent/unparsed')
    url = 'https://ojs.aaai.org/index.php/AAAI/issue/archive'
    seen, primary = set(), {}
    while url:
        if url in seen or len(seen) >= 100:
            raise ValueError('OJS archive pagination loop/limit')
        seen.add(url)
        html = cache.get(url)
        primary.update(aaai_issue_links(html, url, year))
        soup = BeautifulSoup(html, 'html.parser')
        next_page = soup.select_one('.cmp_pagination a.next, a[rel="next"]')
        url = urljoin(url, next_page['href']) if next_page else None
    if primary.keys() != independent_issues.keys():
        collection.issue('AAAI Press and OJS annual issue sets differ')
    write_json(cache.base / 'raw' / 'Issue_Enumeration.json', dict(primary=primary, independent=independent_issues))
    for url in sorted(primary.keys() | independent_issues.keys()):
        html = cache.get(url)
        soup = BeautifulSoup(html, 'html.parser')
        head = soup.select_one('h1')
        m = re.search(r'Vol\. (\d+) No\. (\d+):', head.get_text() if head else '')
        if not m or int(m[1]) != year - 1986:
            raise ValueError('Unrecognized issue scope')
        rows = adapter.index(html, url, year, int(m[1]), int(m[2]))
        collection.publisher.extend(r for r in rows if r['Scope'] == 'TARGET_MAIN')
        collection.excluded.extend(r for r in rows if r['Scope'] == 'NON_TARGET')
        for row in rows:
            if row['Scope'] == 'UNCERTAIN':
                collection.issue('Unknown section: ' + row['Track'], Official_URL=row['Official_URL'])
        collection.save()
    # Enumerate every article in target-containing issues independently, then account for
    # explicitly excluded sections using the already preserved section evidence.
    from aaai_pipeline import OAIIncomplete
    stop_detail_requests=False
    try:
        from aaai_oai_sets import annual_records
        oai = annual_records(cache, year)
        collection.evidence_complete = primary.keys() == independent_issues.keys()
    except OAIIncomplete as exc:
        oai = exc.rows
        stop_detail_requests=exc.stop_requests
        collection.issue('Independent OAI enumeration incomplete: ' + str(exc))
        collection.evidence_complete = False
        if not oai:
            collection.save()
            return
    except (ValueError, ET.ParseError) as exc:
        collection.issue('Independent OAI enumeration failed: ' + str(exc))
        collection.save()
        return
    excluded_urls = {r['Official_URL'] for r in collection.excluded}
    collection.program = [r for r in oai if r['Official_URL'] not in excluded_urls]
    from aaai_pipeline import from_oai
    by_url = {}
    ambiguous = set()
    for row in collection.program:
        url = row['Official_URL']
        if url in by_url:
            ambiguous.add(url)
        by_url[url] = row
    for url in ambiguous:
        by_url.pop(url)
        collection.issue('Multiple official OAI records require detail verification', 'DUPLICATE_IDENTITY', Official_URL=url)
    missing = []
    for row in collection.publisher:
        official=by_url.get(row['Official_URL'])
        if official is not None:
            from venue_audit import authors
            if (not row.get('Authors') or not official.get('Authors')
                    or authors(row['Authors'])!=authors(official['Authors'])):
                collection.issue('Publisher/OAI author metadata missing or conflicting',
                                 Official_URL=row['Official_URL'],Title=row['Title'],
                                 Publisher_Authors=row.get('Authors',''),OAI_Authors=official.get('Authors',''))
        try:
            collection.corpus.append(from_oai(row, by_url[row['Official_URL']], year))
        except (KeyError, ValueError):
            missing.append(row)
    def detail(html, row):
        result = adapter.detail(html, row['Official_URL'], year, int(row['Volume']), int(row['Issue']), utc())
        result['Retrieval_Source'] = row['Official_URL']
        return result
    try:
        if stop_detail_requests:
            collection.issue('AAAI detail requests deferred after official rate/access stop; rerun manually')
            collection.save()
        else:
            collection.details(detail, missing)
    finally:
        from aaai_pipeline import quality_report
        quality_report(collection)


def ecva_index(html, year):
    from urllib.parse import urlparse
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for title in soup.select('dt.ptitle'):
        a = title.select_one('a[href]')
        if not a or f'eccv_{year}/' not in a['href']:
            continue
        author = title.find_next_sibling('dd')
        if not author:
            raise ValueError('ECVA author list missing')
        names = [x.get_text(' ', strip=True) for x in author.select('a')]
        if not names:
            names = author.get_text(' ', strip=True).split(', ')
        # ECVA marks equal contribution with trailing *, not part of the person's name.
        names = [name.rstrip('*').strip() for name in names]
        official=urljoin('https://www.ecva.net/',a['href'])
        parsed=urlparse(official)
        if parsed.scheme!='https' or parsed.hostname not in ('ecva.net','www.ecva.net') or not parsed.path.startswith(f'/papers/eccv_{year}/'):
            raise ValueError('ECVA entry outside official target-year scope')
        rows.append(dict(Title=a.get_text(' ', strip=True),Venue='ECCV',Year=year,
                         Authors='; '.join(names),
                         Official_URL=official,Evidence_URL='https://www.ecva.net/papers.php'))
    if not rows:
        raise ValueError('No target-year ECVA proceedings entries')
    return rows


def springer_books(html, url, year):
    soup = BeautifulSoup(html, 'html.parser')
    return sorted({urljoin(url, a['href']).split('?')[0] for a in soup.select('a[href]')
                   if '/book/10.1007/' in a['href'] and a.get_text(' ', strip=True) == f'Computer Vision – ECCV {year}'})


def springer_declared_counts(html, year):
    """Conference timeline links one representative book, not every volume."""
    soup = BeautifulSoup(html, 'html.parser')
    counts = []
    for item in soup.select('li.app-conference-series-timeline__item'):
        title = item.select_one('[data-test="bookTitle"]')
        if not title or title.get_text(' ', strip=True) != f'Computer Vision – ECCV {year}':
            continue
        entry = {}
        for p in item.select('.app-conference-series-timeline__item-count'):
            value = p.select_one('.app-conference-series-timeline__item-count-value')
            label = p.select_one('.app-conference-series-timeline__item-count-label')
            if value and label:
                entry[label.get_text(strip=True)] = int(value.get_text(strip=True).replace(',', ''))
        counts.append(entry)
    return counts


def eccv(cache, collection):
    year, adapter = cache.year, SpringerECCVAdapter()
    from eccv_access import ECCVAccess
    access=ECCVAccess(cache)
    url = 'https://link.springer.com/conference/eccv'
    queue, seen, roots, declared = [url], set(), set(), []
    while queue:
        url = queue.pop(0)
        url = re.sub(r'\?page=1$', '', url)
        if url in seen:
            continue
        if len(seen) >= 200:
            collection.issue('Springer conference pagination limit; inventory incomplete')
            break
        seen.add(url)
        try:
            html = access.get(url)
            roots.update(springer_books(html, url, year))
            declared.extend(springer_declared_counts(html, year))
            soup = BeautifulSoup(html, 'html.parser')
            queue.extend(urljoin(url, a['href']) for a in soup.select('.c-pagination a[href], a[rel="next"]'))
        except Exception as exc:
            collection.issue('Springer conference inventory unavailable: '+repr(exc),Evidence_URL=url)
    if not roots:
        collection.issue('No official Springer target-year book list')
    try:
        collection.program = ecva_index(access.get('https://www.ecva.net/papers.php'), year)
    except Exception as exc:
        collection.issue('Independent ECVA enumeration unavailable: ' + str(exc))
    if not collection.program and year == 2026:
        from eccv_evidence import accepted_program
        accepted_url=f'https://eccv.ecva.net/Conferences/{year}/AcceptedPapers'
        try:
            collection.program,program_status=accepted_program(access.get(accepted_url),accepted_url,year)
            write_json(cache.base/'raw/Accepted_Program_Evidence.json',program_status)
            collection.issue('Official acceptance list is not a final publication inventory; publisher checks may change membership',
                             Evidence_URL=accepted_url)
        except Exception as exc:
            collection.issue('Official accepted-paper enumeration unavailable: '+repr(exc))
    books, seen, queue, failures = {}, set(), sorted(roots), []
    official_volumes=[];expected_parts={}
    home_checked = year != 2026
    while queue or not home_checked:
        if not queue:
            home_checked = True
            from eccv_evidence import conference_volumes
            home = 'https://eccv.ecva.net/'
            try:
                official_volumes = conference_volumes(access.get(home),home,year)
                write_json(cache.base/'raw/Conference_Volume_Inventory.json',official_volumes)
                known_parts={book['Part'] for book in books.values()}
                expected_parts.update({r['Official_URL']:r['Part'] for r in official_volumes})
                queue.extend(r['Official_URL'] for r in official_volumes if r['Part'] not in known_parts)
            except Exception as exc:
                collection.issue('Official conference volume inventory unavailable: '+repr(exc))
            continue
        url = queue.pop(0)
        url = re.sub(r'\?page=1$', '', url)
        if url in seen:
            continue
        if len(seen) >= 2000:
            collection.issue('Springer book pagination limit; inventory incomplete')
            break
        seen.add(url)
        try:
            book = adapter.book(access.get(url), url, year)
        except Exception as exc:
            failures.append(dict(url=url, error=repr(exc)))
            collection.issue('Springer TOC unavailable: '+repr(exc), Official_URL=url)
            write_json(cache.base / 'raw' / 'Volume_Failures.json', failures)
            continue
        doi = book['Book_DOI']
        if url in expected_parts and expected_parts[url]!=book['Part']:
            collection.issue('Conference volume alias resolves to a different Part',Evidence_URL=url,
                             Declared_Part=expected_parts[url],Publisher_Part=book['Part'])
        for entry in book.get('Non_Chapter_Entries',[]):
            if entry['Title'].casefold() in ('front matter','back matter'):
                collection.excluded.append({**entry,'Publication_Type':'TOC_NON_CHAPTER',
                                            'Reason':'Official publisher TOC explicitly labels non-chapter material; not counted as a chapter'})
            else:
                collection.issue('Unclassified non-chapter TOC entry',**entry)
        for link in book.get('Untrusted_Volume_Links',[]):
            collection.issue('Other-volume link outside official book scope',Evidence_URL=url,Linked_URL=link)
        if 'page=' not in url:
            seen.add('https://link.springer.com/book/'+doi)
        previous = books.get(doi)
        if previous:
            if any(previous[k] != book[k] for k in ('Part', 'LNCS_Volume', 'Declared_Chapter_Count')):
                collection.issue('Book metadata differs across pages', Book_DOI=doi)
            previous['Chapters'].extend(book['Chapters'])
        else:
            books[doi] = book
        books[doi].setdefault('TOC_Page_URLs',[]).append(url)
        write_json(cache.base / 'raw' / 'Volume_Enumeration.json', dict(
            status='ENUMERATING', conference_seed_books=sorted(roots), conference_declared_counts=declared, books=books))
        queue.extend(book['Pages'])
        queue.extend(book['Other_Volumes'])
    parts = set();lncs_volumes=set()
    for doi, book in books.items():
        if book['Part'] in parts:
            collection.issue('Duplicate LNCS part', 'DUPLICATE_IDENTITY', Book_DOI=doi)
        parts.add(book['Part'])
        if book['LNCS_Volume'] in lncs_volumes:
            collection.issue('Duplicate LNCS volume identity','DUPLICATE_IDENTITY',Book_DOI=doi,LNCS_Volume=book['LNCS_Volume'])
        lncs_volumes.add(book['LNCS_Volume'])
        unique = {}
        for chapter in book['Chapters']:
            if chapter['Official_URL'] in unique:
                collection.issue('Duplicate chapter across TOC pages', 'DUPLICATE_IDENTITY', Official_URL=chapter['Official_URL'])
            unique[chapter['Official_URL']] = chapter
        if len(unique) != book['Declared_Chapter_Count']:
            collection.issue('Enumerated chapters do not match declared TOC count', Book_DOI=doi)
        collection.publisher.extend(unique.values())
    write_json(cache.base / 'raw' / 'Volume_Enumeration.json', dict(conference_seed_books=sorted(roots), conference_declared_counts=declared, books=books))
    volume_counts = {d['Volumes'] for d in declared if 'Volumes' in d}
    paper_counts = {d['Papers'] for d in declared if 'Papers' in d}
    if volume_counts:
        if volume_counts != {len(books)}:
            collection.issue(f'Conference declared volume counts {sorted(volume_counts)} differ from enumerated {len(books)}')
    elif not official_volumes or {r['Part'] for r in official_volumes} != parts:
        collection.issue('No complete official volume inventory/count corroborates discovered other-volumes')
    if official_volumes and {r['Part'] for r in official_volumes} != parts:
        collection.issue('Conference Part inventory and publisher Parts differ')
    if paper_counts and paper_counts != {len(collection.publisher)}:
        collection.issue(f'Conference declared paper counts {sorted(paper_counts)} differ from enumerated {len(collection.publisher)}')
    write_csv(cache.base/'raw/Publisher_All_Chapter_Records.csv',collection.publisher)
    from eccv_evidence import nonpaper_candidate,nonpaper_evidence
    def detail(html, row):
        result = adapter.detail(html, row['Official_URL'], year, row['Book_DOI'], utc())
        # TOC authors may be truncated. Reconciliation uses full chapter citation authors.
        row['Authors'] = result['Authors']
        row['TOC_Title'],row['Title']=row['Title'],result['Title']
        row['Paper_ID'],row['DOI']=result['Paper_ID'],result['DOI']
        result['Retrieval_Source']=row['Official_URL']
        result['Part'],result['LNCS_Volume']=books[row['Book_DOI']]['Part'],books[row['Book_DOI']]['LNCS_Volume']
        return result
    collection.save()
    excluded_urls=set()
    try:
        for number,row in enumerate(collection.publisher,1):
            try:
                html=access.get(row['Official_URL'],cache_only=bool(failures))
                if nonpaper_candidate(row['Title']):
                    proof=nonpaper_evidence(html,row['Official_URL'],year)
                    if not proof:raise ValueError('Non-paper candidate lacks sufficient official classification evidence')
                    collection.excluded.append(proof);excluded_urls.add(row['Official_URL'])
                else:
                    result=detail(html,row)
                    if nonpaper_candidate(result['Title']):
                        proof=nonpaper_evidence(html,row['Official_URL'],year)
                        if not proof:raise ValueError('Chapter metadata suggests unclassified non-paper record')
                        collection.excluded.append(proof);excluded_urls.add(row['Official_URL'])
                    else:
                        collection.corpus.append(result)
            except Exception as exc:
                collection.issue('Chapter unavailable/unresolved: '+repr(exc),Official_URL=row['Official_URL'],Title=row['Title'])
            if number%25==0 or number==len(collection.publisher):
                print(f'ECCV {year}: details {number}/{len(collection.publisher)}; cache hits={cache.hits}',flush=True)
                collection.save()
    finally:
        collection.publisher=[r for r in collection.publisher if r['Official_URL'] not in excluded_urls]
        collection.save()
    # Fetch only unresolved independent identities. Their own official DOI links
    # can corroborate major publication-title changes without fuzzy inference.
    from eccv_identity import reconcile_eccv
    from eccv_evidence import publication_link
    preliminary,_=reconcile_eccv(collection.publisher,collection.program)
    pending={r.get('Independent_URL') for r in preliminary if r['Status']=='ECVA_ONLY_UNRESOLVED'}
    link_evidence=[]
    for row in collection.program:
        if row.get('Official_URL') not in pending or 'www.ecva.net/papers/' not in row.get('Official_URL',''):continue
        try:
            proof=publication_link(access.get(row['Official_URL']),row['Official_URL'],year,row['Title'])
            row.update(proof)
            link_evidence.append(dict(Independent_URL=row['Official_URL'],**proof))
        except Exception as exc:
            # Failed auxiliary identity evidence is not a missing paper. The
            # unresolved comparison remains in the main audit.
            link_evidence.append(dict(Independent_URL=row['Official_URL'],error=repr(exc)))
        write_json(cache.base/'raw/ECVA_Publication_Links.json',link_evidence)
    collection.save()
    collection.evidence_complete = bool(collection.program) and not failures and not access.blocked and not collection.issues


from tpami_pipeline import tpami

PIPELINES = {'ICML': icml, 'ICLR': iclr, 'AAAI': aaai, 'ECCV': eccv, 'TPAMI': tpami}
