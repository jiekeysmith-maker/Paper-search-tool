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
