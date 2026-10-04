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
    """Independent article enumeration through the publisher's OAI-PMH export.

    Datestamps are modification dates, so no publication-year date filter is used.
    Unknown source metadata fails closed instead of dropping potentially relevant papers.
    """
    root = 'https://ojs.aaai.org/index.php/AAAI/oai'
    url = root + '?verb=ListRecords&metadataPrefix=oai_dc'
    seen, rows = set(), []
    ns = {'o': 'http://www.openarchives.org/OAI/2.0/', 'dc': 'http://purl.org/dc/elements/1.1/'}
    while url:
        if url in seen or len(seen) >= 10000:
            raise ValueError('OAI pagination loop/limit')
        seen.add(url)
        tree = ET.fromstring(cache.get(url))
        if tree.find('o:error', ns) is not None:
            raise ValueError('OAI export error')
        records = tree.findall('.//o:record', ns)
        if not records:
            raise ValueError('Empty OAI page')
        for record in records:
            header = record.find('o:header', ns)
            if header is not None and header.get('status') == 'deleted':
                continue
            sources = [x.text or '' for x in record.findall('.//dc:source', ns)]
            english = next((s for s in sources if 'Proceedings of the AAAI Conference on Artificial Intelligence' in s), '')
            scope = re.search(r'Vol\.?\s*(\d+)\D+No\.?\s*(\d+)', english, re.I)
            if not scope:
                raise ValueError('OAI source cannot establish volume/issue scope')
            if int(scope[1]) != year - 1986 or int(scope[2]) not in target_issues:
                continue
            ids = [x.text or '' for x in record.findall('.//dc:identifier', ns)]
            article = next((u for u in ids if re.fullmatch(r'https://ojs.aaai.org/index.php/AAAI/article/view/\d+', u)), '')
            if not article:
                raise ValueError('OAI missing stable article URL')
            titles = [x.text or '' for x in record.findall('.//dc:title', ns)]
            rows.append(dict(Title=titles[0] if titles else '', Official_URL=article,
                             Authors='; '.join(x.text or '' for x in record.findall('.//dc:creator', ns)),
                             Issue=int(scope[2]), Evidence_URL=url))
        token = tree.find('.//o:resumptionToken', ns)
        url = root + '?' + urlencode({'verb': 'ListRecords', 'resumptionToken': token.text}) if token is not None and token.text else None
    return rows


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
    try:
        target_issues = {int(r['Issue']) for r in collection.publisher}
        oai = aaai_oai(cache, year, target_issues)
        excluded_urls = {r['Official_URL'] for r in collection.excluded}
        collection.program = [r for r in oai if r['Official_URL'] not in excluded_urls]
        collection.evidence_complete = primary.keys() == independent_issues.keys()
    except (ValueError, ET.ParseError) as exc:
        collection.issue('Independent OAI enumeration failed: ' + str(exc))
    collection.details(lambda html, row: adapter.detail(html, row['Official_URL'], year, int(row['Volume']), int(row['Issue']), utc()))


def ecva_index(html, year):
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
        rows.append(dict(Title=a.get_text(' ', strip=True),
                         Authors='; '.join(names),
                         Official_URL=urljoin('https://www.ecva.net/', a['href']), Evidence_URL='https://www.ecva.net/papers.php'))
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
    url = 'https://link.springer.com/conference/eccv'
    queue, seen, roots, declared = [url], set(), set(), []
    while queue:
        url = queue.pop(0)
        url = re.sub(r'\?page=1$', '', url)
        if url in seen:
            continue
        if len(seen) >= 200:
            raise ValueError('Springer conference pagination limit')
        seen.add(url)
        html = cache.get(url)
        roots.update(springer_books(html, url, year))
        declared.extend(springer_declared_counts(html, year))
        soup = BeautifulSoup(html, 'html.parser')
        queue.extend(urljoin(url, a['href']) for a in soup.select('.c-pagination a[href], a[rel="next"]'))
    if not roots:
        raise ValueError('No official Springer target-year book list')
    collection.program = ecva_index(cache.get('https://www.ecva.net/papers.php'), year)
    books, seen, queue = {}, set(), sorted(roots)
    while queue:
        url = queue.pop(0)
        url = re.sub(r'\?page=1$', '', url)
        if url in seen:
            continue
        if len(seen) >= 2000:
            raise ValueError('Springer book pagination limit')
        seen.add(url)
        book = adapter.book(cache.get(url), url, year)
        doi = book['Book_DOI']
        previous = books.get(doi)
        if previous:
            if any(previous[k] != book[k] for k in ('Part', 'LNCS_Volume', 'Declared_Chapter_Count')):
                collection.issue('Book metadata differs across pages', Book_DOI=doi)
            previous['Chapters'].extend(book['Chapters'])
        else:
            books[doi] = book
        write_json(cache.base / 'raw' / 'Volume_Enumeration.json', dict(
            status='ENUMERATING', conference_seed_books=sorted(roots), conference_declared_counts=declared, books=books))
        queue.extend(book['Pages'])
        queue.extend(book['Other_Volumes'])
    parts = set()
    for doi, book in books.items():
        if book['Part'] in parts:
            collection.issue('Duplicate LNCS part', 'DUPLICATE_IDENTITY', Book_DOI=doi)
        parts.add(book['Part'])
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
    elif roots != {'https://link.springer.com/book/' + doi for doi in books}:
        collection.issue('No complete official volume inventory/count corroborates discovered other-volumes')
    if paper_counts and paper_counts != {len(collection.publisher)}:
        collection.issue(f'Conference declared paper counts {sorted(paper_counts)} differ from enumerated {len(collection.publisher)}')
    def detail(html, row):
        result = adapter.detail(html, row['Official_URL'], year, row['Book_DOI'], utc())
        # TOC authors may be truncated. Reconciliation uses full chapter citation authors.
        row['Authors'] = result['Authors']
        return result
    collection.details(detail)
    collection.evidence_complete = True


PIPELINES = {'ICML': icml, 'ICLR': iclr, 'AAAI': aaai, 'ECCV': eccv}
