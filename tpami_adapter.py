"""Strict IEEE public metadata/HTML adapter; never evaluates site JavaScript.

Metadata field names follow IEEE's documented Metadata API and citation tags.
Empty SPA shells are not annual directories. API credentials are not fabricated.
"""
import json
import re
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from tpami_policy import TITLE, YEAR_BASIS, doi_key


class TPAMIAdapter:
    def volume_directory(self, html, url, year):
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ', strip=True)
        if TITLE not in text or str(year) not in text:
            raise ValueError('IEEE annual directory lacks target journal/year evidence (possibly SPA shell)')
        rows = []
        for a in soup.select('a[href]'):
            link = urljoin(url, a['href'])
            p = urlparse(link)
            if p.hostname not in ('ieeexplore.ieee.org', 'www.computer.org'):
                continue
            if not ('isnumber' in parse_qs(p.query) or re.fullmatch(rf'/csdl/journal/tp/{year}/\d{{2}}', p.path)):
                continue
            context = a.parent.get_text(' ', strip=True)
            volume = re.search(r'Volume\s*:?\s*(\d+)', context, re.I)
            issue = re.search(r'Issue\s*:?\s*(\d+)', context, re.I)
            date = re.search(rf'{year}-\d{{2}}(?:-\d{{2}})?', context)
            if not volume or not issue or not date:
                raise ValueError('Issue link missing explicit volume/issue/publication date')
            rows.append(dict(Year=year, Volume=volume[1], Issue=issue[1],
                             Issue_Publication_Date=date[0], Issue_URL=link, Publication_Number='34'))
        if not rows:
            raise ValueError('No independently enumerable IEEE final issues; no REST fallback')
        keys = [(r['Volume'], r['Issue']) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate annual issue identity')
        return rows

    def issue_index(self, html, url, context):
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ', strip=True)
        if TITLE not in text or not re.search(r'Volume\s*:?\s*' + re.escape(context['Volume']) + r'\b', text, re.I):
            raise ValueError('Issue page publication/volume mismatch')
        if not re.search(r'Issue\s*:?\s*' + re.escape(context['Issue']) + r'\b', text, re.I):
            raise ValueError('Issue page issue mismatch')
        rows = []
        for a in soup.select('a[href]'):
            link = urljoin(url, a['href'])
            identity = re.fullmatch(r'https://ieeexplore\.ieee\.org/(?:abstract/)?document/(\d+)/?', link)
            if identity:
                rows.append(dict(Title=a.get_text(' ', strip=True), Official_URL=f'https://ieeexplore.ieee.org/document/{identity[1]}',
                                 Native_Publisher_ID=identity[1], **context))
        if not rows or len({r['Native_Publisher_ID'] for r in rows}) != len(rows):
            raise ValueError('Empty/duplicate IEEE issue article enumeration')
        count = re.search(r'(\d+)\s+(?:Articles|Documents)', text, re.I)
        if not count or int(count[1]) != len(rows):
            raise ValueError('IEEE issue count missing or pagination incomplete')
        return rows

    def metadata(self, data, context):
        def field(snake, camel=None, default=''):
            return data.get(snake, data.get(camel, default) if camel else default)
        if field('publication_title', 'publicationTitle') != TITLE:
            raise ValueError('Not TPAMI')
        if str(field('publication_number', 'publicationNumber')) != '34':
            raise ValueError('Wrong IEEE publication identity')
        if field('content_type', 'contentType') == 'Early Access' or not field('volume') or not field('issue'):
            raise ValueError('EARLY_ACCESS_UNASSIGNED: excluded from final issue corpus')
        if field('content_type', 'contentType') != 'Journals':
            raise ValueError('Unverified journal article type')
        year = int(context['Year'])
        if (str(field('volume')) != str(context['Volume']) or str(field('issue')) != str(context['Issue'])
                or str(field('publication_year', 'publicationYear')) != str(year)):
            raise ValueError('Final issue year/volume/issue conflicts with document metadata')
        doi = doi_key(field('doi'))
        if not doi.startswith('10.1109/tpami.'):
            raise ValueError('Missing TPAMI DOI')
        native = str(field('article_number', 'articleNumber'))
        if not native.isdigit():
            raise ValueError('Missing IEEE document identity')
        names = field('authors', default=[])
        if isinstance(names, dict):
            names = names.get('authors', [])
        authors = '; '.join(n if isinstance(n, str) else n.get('full_name', n.get('name', '')) for n in names)
        title, abstract = field('title'), field('abstract')
        if not all(isinstance(x, str) and x.strip() for x in (title, abstract, authors)):
            raise ValueError('Incomplete title/authors/abstract')
        return dict(Paper_ID=f'TPAMI_IEEE_{native}', Title=title, Authors=authors,
                    Abstract=BeautifulSoup(abstract, 'html.parser').get_text(' ', strip=True), Venue='TPAMI', Year=year,
                    Track=field('article_type', 'articleType') or 'Journal Article', Official_URL=f'https://ieeexplore.ieee.org/document/{native}',
                    PDF_URL=field('pdf_url', 'pdfUrl'), DOI=doi, Volume=str(context['Volume']), Issue=str(context['Issue']),
                    Year_Basis=YEAR_BASIS, Final_Issue_Year=year, Issue_Publication_Date=context['Issue_Publication_Date'],
                    Published_Date=context['Issue_Publication_Date'], Publisher_Publication_Date=field('publication_date', 'publicationDate'),
                    Early_Access_Date=field('early_access_date', 'earlyAccessDate'), Native_Publisher_ID=native,
                    Formal_Publication_Evidence=f'IEEE TPAMI; final volume {context["Volume"]}, issue {context["Issue"]}, {year}; {context["Issue_URL"]}; DOI {doi}',
                    BibTeX_or_Publisher_Metadata=json.dumps(data, ensure_ascii=False))

    def detail(self, html, url, context):
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.select('script'):
            match = re.search(r'xplGlobal\.document\.metadata\s*=\s*', script.get_text())
            if match:
                data, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():].lstrip())
                row = self.metadata(data, context)
                if row['Official_URL'].rstrip('/') != url.rstrip('/'):
                    raise ValueError('Document URL/identity mismatch')
                return row
        raise ValueError('No public IEEE document metadata; no fabricated abstract or restricted REST request')
