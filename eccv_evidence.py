"""ECCV-specific official volume inventory and non-paper evidence."""
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


def accepted_program(html,url,year):
    """Official acceptance evidence, explicitly not a final publication census."""
    soup=BeautifulSoup(html,'html.parser');text=soup.get_text(' ',strip=True)
    if not re.search(rf'ECCV\s+{year}\s+Accepted Papers',text):
        raise ValueError('Accepted-paper source year/title mismatch')
    table=soup.select_one(f'table#event-list-{year}-poster-nodates-filter-vslinks-table')
    if table is None:raise ValueError('Official accepted-paper table absent')
    rows=[];seen=set();duplicates=[]
    for tr in table.select('tr'):
        cells=tr.find_all('td',recursive=False)
        if not cells:continue
        link=cells[0].find('a',href=re.compile(rf'^/virtual/{year}/poster/\d+$'))
        if link is None:raise ValueError('Unparsed accepted-paper table row')
        identity=link['href'].rsplit('/',1)[-1]
        if identity in seen:duplicates.append(identity)
        seen.add(identity);names=cells[0].select_one('.indented i')
        rows.append(dict(Title=link.get_text(' ',strip=True),Authors=names.get_text(' ',strip=True).replace(' ⋅ ','; ') if names else '',
                         Official_URL=urljoin(url,link['href']),Evidence_URL=url,Program_Event_ID=identity,
                         Retrieval_Source=url,Publication_Status='ACCEPTED_PROGRAM_NOT_FINAL_PUBLICATION'))
    if not rows:raise ValueError('Empty official acceptance enumeration')
    return rows,dict(source=url,enumerated_count=len(rows),unique_event_count=len(seen),duplicate_event_ids=duplicates,
                     preliminary='preliminary pending publisher checks' in text.casefold(),
                     final_publication_inventory=False)


def conference_volumes(html,url,year):
    soup=BeautifulSoup(html,'html.parser')
    if not re.search(rf'ECCV\s*{year}\b',soup.get_text(' ',strip=True)):
        raise ValueError('Official conference page does not establish target year')
    rows=[]
    for a in soup.select('a[href]'):
        part=a.get_text(' ',strip=True);link=urljoin(url,a['href']);p=urlparse(link)
        if (re.fullmatch('[IVXLCDM]+',part) and p.scheme=='https'
                and p.hostname=='link.springer.com' and p.path.startswith('/book/')):
            rows.append(dict(Part=part,Official_URL=link,Evidence_URL=url))
    if not rows or len({r['Part'] for r in rows})!=len(rows):
        raise ValueError('Missing/duplicate official volume Part inventory')
    return rows


def publication_link(html,url,year,expected_title):
    """An ECVA paper's explicit DOI link, never a reference-list DOI."""
    from eccv_identity import title_key
    parsed=urlparse(url)
    if parsed.hostname!='www.ecva.net' or not parsed.path.startswith(f'/papers/eccv_{year}/papers_ECCV/html/'):
        raise ValueError('Wrong ECVA paper/year scope')
    soup=BeautifulSoup(html,'html.parser')
    title=soup.select_one('#papertitle');abstract=soup.select_one('#abstract')
    if not title or title_key(title.get_text(' ',strip=True))!=title_key(expected_title) or not abstract:
        raise ValueError('ECVA detail identity does not match enumeration')
    targets=set()
    for a in soup.select('#content a[href]'):
        if a.get_text(' ',strip=True).casefold()!='doi' or a.find_parent(id='abstract'):continue
        target=urljoin(url,a['href'])
        if re.fullmatch(r'https://link\.springer\.com/chapter/10\.1007/[^?#\s]+',target):targets.add(target)
    if len(targets)!=1:raise ValueError('Missing/ambiguous explicit ECVA publication DOI link')
    target=targets.pop()
    return dict(DOI=target.split('/chapter/')[1],Publication_Link=target,
                Publication_Link_Evidence=url,Publication_Link_Type='ECVA_EXPLICIT_DOI')


def correction_evidence(html,url,year):
    soup=BeautifulSoup(html,'html.parser')
    def meta(name):
        item=soup.select_one(f'meta[name="{name}"]')
        return item.get('content','') if item else ''
    title=meta('citation_title');doi=meta('citation_doi')
    text=soup.get_text(' ',strip=True)
    original=re.search(r'The updated version of this chapter can be found at\s+(https://doi.org/10\.1007/[^\s]+)',text)
    if (title.startswith(('Correction to:','Erratum to:')) and re.fullmatch(r'C\d+',meta('citation_firstpage'))
            and meta('citation_inbook_title')==f'Computer Vision – ECCV {year}'
            and url=='https://link.springer.com/chapter/'+doi and original
            and original[1]!='https://doi.org/'+doi):
        return dict(Title=title,Official_URL=url,DOI=doi,Original_Chapter_URL=original[1],
                    Reason='Official correction notice, C-page pagination, explicit original chapter link',
                    Publication_Type='CORRECTION_NOTICE',Evidence_URL=url)
    return None


def nonpaper_candidate(title):
    return bool(re.match(r'^(?:Correction to:|Erratum to:|(?:Correction|Erratum|Front Matter|Back Matter)$)',title,re.I))


def nonpaper_evidence(html,url,year):
    proof=correction_evidence(html,url,year)
    if proof:return proof
    soup=BeautifulSoup(html,'html.parser')
    def meta(name):
        item=soup.select_one(f'meta[name="{name}"]')
        return item.get('content','') if item else ''
    kind=meta('citation_section');doi=meta('citation_doi')
    if (kind in ('Front Matter','Back Matter') and meta('citation_title')==kind
            and meta('citation_inbook_title')==f'Computer Vision – ECCV {year}'
            and url=='https://link.springer.com/chapter/'+doi
            and re.fullmatch('[ivxlcdmIVXLCDM]+',meta('citation_firstpage'))):
        return dict(Title=kind,Official_URL=url,DOI=doi,Publication_Type='NON_PAPER_MATTER',Evidence_URL=url,
                    Reason='Official citation_section, title and Roman pagination identify non-paper matter')
    return None
