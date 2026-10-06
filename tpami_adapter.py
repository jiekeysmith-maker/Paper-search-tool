"""Strict IEEE public metadata/HTML adapter; never evaluates site JavaScript.

Metadata field names follow IEEE's documented Metadata API and citation tags.
Empty SPA shells are not annual directories. API credentials are not fabricated.
"""
import json
import re
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from tpami_policy import TITLE, YEAR_BASIS, doi_key, normalize_date


class NonResearchArticle(ValueError):
    def __init__(self,proof):
        super().__init__('Official non-research publication type')
        self.proof=proof


class EarlyAccessRecord(NonResearchArticle):
    def __init__(self,proof):
        ValueError.__init__(self,'EARLY_ACCESS_UNASSIGNED: publication event, not a final-year paper')
        self.proof=proof


class TPAMIAdapter:
    def volume_directory(self, html, url, year):
        from tpami_public_pages import csdl_directory, ieee_directory
        rendered=csdl_directory(html,url,year)
        if rendered is None:rendered=ieee_directory(html,url,year)
        if rendered is not None:return rendered
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

    def inventory_evidence(self,html,rows):
        from tpami_public_pages import rendered_inventory_evidence
        rendered=rendered_inventory_evidence(html,rows)
        if rendered is not None:return rendered
        text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
        counts={int(n) for n in re.findall(r'\b(\d+)\s+issues\b',text,re.I)}
        # Matching two partial lists does not prove either inventory is complete.
        return dict(declared_issue_counts=sorted(counts),discovered_issue_count=len(rows),
                    complete=bool(rows) and counts=={len(rows)})

    def issue_page(self, html, url, context):
        from tpami_public_pages import csdl_issue_page, ieee_issue_page
        rendered=csdl_issue_page(html,url,context)
        if rendered is None:rendered=ieee_issue_page(html,url,context)
        if rendered is not None:return rendered
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ', strip=True)
        if not context.get('Volume'):
            volumes=set(re.findall(r'Volume\s*:?\s*(\d+)\b',text,re.I))
            if len(volumes)!=1:raise ValueError('Issue page volume absent or ambiguous')
            context={**context,'Volume':volumes.pop()}
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
        pages=[]
        for a in soup.select('a[rel="next"], .pagination a[href]'):
            link=urljoin(url,a['href']);p=urlparse(link);origin=urlparse(context['Issue_URL'])
            if p.hostname!=origin.hostname or p.path!=origin.path or p.scheme!='https':
                raise ValueError('Pagination link escapes official issue')
            if origin.hostname=='ieeexplore.ieee.org' and parse_qs(p.query).get('isnumber')!=parse_qs(origin.query).get('isnumber'):
                raise ValueError('Pagination link changes issue identity')
            if link!=url:pages.append(link)
        return dict(rows=rows,declared_count=int(count[1]) if count else None,pages=sorted(set(pages)),context=context)

    def issue_index(self,html,url,context):
        page=self.issue_page(html,url,context)
        if page['declared_count']!=len(page['rows']) or page['pages']:
            raise ValueError('IEEE issue count missing or pagination incomplete')
        return page['rows']

    def metadata(self, data, context):
        def field(snake, camel=None, default=''):
            return data.get(snake, data.get(camel, default) if camel else default)
        if field('publication_title', 'publicationTitle') != TITLE:
            raise ValueError('Not TPAMI')
        if str(field('publication_number', 'publicationNumber')) != '34':
            raise ValueError('Wrong IEEE publication identity')
        if field('content_type', 'contentType') == 'Early Access' or data.get('isEarlyAccess') is True:
            if field('volume') and field('issue'):
                raise ValueError('Early Access flag conflicts with an assigned final volume/issue')
            doi=doi_key(field('doi'));native=str(field('article_number','articleNumber'))
            if not doi.startswith('10.1109/tpami.') or not native.isdigit():raise ValueError('Unassigned record lacks stable TPAMI identity')
            raise EarlyAccessRecord(dict(Title=field('title'),DOI=doi,Native_Publisher_ID=native,
                Official_URL=f'https://ieeexplore.ieee.org/document/{native}',Publication_Type='EARLY_ACCESS_UNASSIGNED',
                Early_Access_Date=field('early_access_date','earlyAccessDate'),
                Publisher_Publication_Date=field('publication_date','publicationDate'),
                Reason='Official record has no final issue assignment; no corpus year inferred',
                BibTeX_or_Publisher_Metadata=json.dumps(data,ensure_ascii=False)))
        if not field('volume') or not field('issue'):
            raise ValueError('Missing final issue assignment; no explicit Early Access classification')
        # Public Xplore HTML uses "periodicals", unlike the Metadata API.
        # Accept this observed schema only with its affirmative journal flags.
        public_page = (field('content_type', 'contentType') == 'periodicals'
                       and data.get('contentTypeDisplay') == 'Journals'
                       and data.get('isJournal') is True)
        if field('content_type', 'contentType') != 'Journals' and not public_page:
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
        issue_date=normalize_date(context['Issue_Publication_Date'])
        if not issue_date.startswith(str(year)):raise ValueError('Final issue publication date/year conflict')
        if public_page:
            published=normalize_date(field('publication_date','publicationDate'))
            if not published or published[:7]!=issue_date[:7]:
                raise ValueError('Public document final issue date conflicts with issue directory')
            issue_ids=parse_qs(urlparse(context['Issue_URL']).query).get('isnumber',[])
            if issue_ids and str(data.get('isNumber','')) not in issue_ids:
                raise ValueError('Public document final issue identity mismatch')
            if data.get('isEphemera') is True:
                raise ValueError('Official ephemera flag requires explicit non-research classification')
        kind=str(field('article_type','articleType')).strip()
        if kind.casefold() in {'editorial','front matter','correction','corrections','erratum','index','cover','masthead','announcements'}:
            raise NonResearchArticle({**context,'Title':field('title'),'DOI':doi,'Native_Publisher_ID':native,
                'Official_URL':f'https://ieeexplore.ieee.org/document/{native}','Publication_Type':kind,
                'Reason':'Explicit official article type and final issue assignment'})
        if re.match(r'^(?:correction to|erratum|editorial|front matter|masthead)\b',str(field('title')),re.I):
            raise ValueError('Possible non-research article lacks explicit official type evidence')
        names = field('authors', default=[])
        if isinstance(names, dict):
            names = names.get('authors', [])
        names=[n if isinstance(n,str) else n.get('full_name',n.get('name','')) for n in names]
        if not names or any(not isinstance(n,str) or not n.strip() for n in names):raise ValueError('Incomplete author identities')
        authors = '; '.join(names)
        title, abstract = field('title'), field('abstract')
        if not all(isinstance(x, str) and x.strip() for x in (title, abstract, authors)):
            raise ValueError('Incomplete title/authors/abstract')
        abstract=BeautifulSoup(abstract,'html.parser').get_text(' ',strip=True)
        from eccv_metadata import abstract_issue
        if abstract_issue(title,abstract):raise ValueError(abstract_issue(title,abstract))
        ea_raw=field('early_access_date','earlyAccessDate')
        # displayPublicationDate labels "Date of Publication" in public HTML;
        # publicationDate labels the final issue month. Never confuse the two.
        online_raw=data.get('displayPublicationDate','') if public_page else field('publication_date','publicationDate')
        ea=normalize_date(ea_raw)
        online=normalize_date(online_raw)
        if (ea_raw and not ea) or (online_raw and not online):
            raise ValueError('Unparseable reported publication date; evidence requires review')
        dates=[d for d in (ea,online) if d]
        first=min(dates) if dates else ''
        precision=min(len(first),len(issue_date))
        if first and first[:precision]>issue_date[:precision]:raise ValueError('First publication occurs after assigned issue date')
        return dict(Paper_ID=f'TPAMI_IEEE_{native}', Title=title, Authors=authors,
                    Abstract=abstract, Venue='TPAMI', Year=year,
                    Track=field('article_type', 'articleType') or 'Journal Article', Official_URL=f'https://ieeexplore.ieee.org/document/{native}',
                    PDF_URL=urljoin('https://ieeexplore.ieee.org',field('pdf_url', 'pdfUrl')) if field('pdf_url','pdfUrl') else '', DOI=doi, Volume=str(context['Volume']), Issue=str(context['Issue']),
                    Year_Basis=YEAR_BASIS, Final_Issue_Year=year, Issue_Publication_Date=context['Issue_Publication_Date'],
                    Published_Date=context['Issue_Publication_Date'], Publisher_Publication_Date=online_raw,
                    Publisher_Issue_Date=field('publication_date','publicationDate') if public_page else '',
                    Early_Access_Date=field('early_access_date', 'earlyAccessDate'), Native_Publisher_ID=native,
                    First_Official_Publication_Date=first,First_Publication_Date_Evidence='Earliest explicitly reported publisher/early-access date; no inferred dates',
                    Year_Assignment_Evidence=f'Final issue assignment {context["Issue_URL"]}; Early Access never assigns corpus year',
                    Formal_Publication_Evidence=f'IEEE TPAMI; final volume {context["Volume"]}, issue {context["Issue"]}, {year}; {context["Issue_URL"]}; DOI {doi}',
                    BibTeX_or_Publisher_Metadata=json.dumps(data, ensure_ascii=False))

    def detail(self, html, url, context):
        soup = BeautifulSoup(html, 'html.parser')
        def checked(data):
            try:row=self.metadata(data,context)
            except NonResearchArticle as exc:
                if exc.proof['Official_URL'].rstrip('/')!=url.rstrip('/'):raise ValueError('Non-research document URL/identity mismatch')
                raise
            if row['Official_URL'].rstrip('/')!=url.rstrip('/'):raise ValueError('Document URL/identity mismatch')
            row['Retrieval_Source']=url
            row['Abstract_Source_URL']=url
            return row
        for script in soup.select('script'):
            match = re.search(r'xplGlobal\.document\.metadata\s*=\s*', script.get_text())
            if match:
                data, _ = json.JSONDecoder().raw_decode(script.get_text()[match.end():].lstrip())
                return checked(data)
        tags={}
        for tag in soup.select('meta[name^="citation_"]'):
            tags.setdefault(tag['name'],[]).append(tag.get('content',''))
        def tag(name):return (tags.get('citation_'+name) or [''])[0]
        # Citation-only pages need independent journal identity and explicit
        # final issue metadata; never assign the context year to missing fields.
        if tag('journal_title')==TITLE and tag('issn') in ('0162-8828','1939-3539'):
            date=normalize_date(tag('publication_date'))
            identity=re.fullmatch(r'https://ieeexplore\.ieee\.org/document/(\d+)/?',url)
            if not identity or not date:raise ValueError('Incomplete citation publication identity/date')
            return checked(dict(publication_title=tag('journal_title'),publication_number=34,content_type='Journals',
                volume=tag('volume'),issue=tag('issue'),publication_year=date[:4],doi=tag('doi'),
                article_number=identity[1],authors=tags.get('citation_author',[]),title=tag('title'),
                abstract=tag('abstract'),publication_date=tag('online_date') or tag('publication_date'),
                early_access_date=tag('online_date'),pdf_url=tag('pdf_url'),article_type=tag('article_type')))
        raise ValueError('No public IEEE document metadata; no fabricated abstract or restricted REST request')
