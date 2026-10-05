"""ECCV-specific official volume inventory and non-paper evidence."""
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


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
    if (title.startswith('Correction to:') and re.fullmatch(r'C\d+',meta('citation_firstpage'))
            and meta('citation_inbook_title')==f'Computer Vision – ECCV {year}'
            and url=='https://link.springer.com/chapter/'+doi and original
            and original[1]!='https://doi.org/'+doi):
        return dict(Title=title,Official_URL=url,DOI=doi,Original_Chapter_URL=original[1],
                    Reason='Official correction notice, C-page pagination, explicit original chapter link',
                    Publication_Type='CORRECTION_NOTICE',Evidence_URL=url)
    return None
