"""Platform-specific public metadata parsers. No CVF parser reuse here."""
import re, json
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

class PMLRAdapter:
    def index(self, html, url, year):
        s=BeautifulSoup(html,'html.parser')
        heading=s.get_text(' ',strip=True)[:1800]
        if 'International Conference on Machine Learning' not in heading or str(year) not in heading:
            raise ValueError('Wrong PMLR conference/year')
        rows=[]
        for p in s.select('div.paper'):
            a=next((a for a in p.select('a[href]') if a.get_text(strip=True)=='abs'),None)
            if not a: raise ValueError('Missing detail link')
            rows.append(dict(Title=p.select_one('.title').get_text(' ',strip=True),Official_URL=urljoin(url,a['href']),Authors=p.select_one('.authors').get_text(' ',strip=True),OpenReview_URL=next((a['href'] for a in p.select('a[href]') if 'openreview.net/forum?' in a['href']),'')))
        if not rows: raise ValueError('Empty/missing PMLR index')
        if len({r['Official_URL'] for r in rows})!=len(rows): raise ValueError('Duplicate index URL')
        return rows
    def classify_scope(self, row, year, volume):
        url=urlparse(row.get('URL',''))
        date=row.get('issued',{}).get('date-parts',[])
        if date and isinstance(date[0],list): date=date[0]
        if not date or int(date[0])!=year: return 'NON_TARGET'
        if str(row.get('volume'))!=str(volume): return 'NON_TARGET'
        if url.hostname!='proceedings.mlr.press' or not url.path.startswith(f'/v{volume}/'): return 'NON_TARGET'
        title=row.get('container-title','')
        if not re.fullmatch(r'Proceedings of the \d+(st|nd|rd|th) International Conference on Machine Learning',title): return 'NON_TARGET'
        return 'TARGET_MAIN'
    def normalize(self,row,year,volume,source,stamp):
        if self.classify_scope(row,year,volume)!='TARGET_MAIN': raise ValueError('Wrong scope')
        authors='; '.join(' '.join(filter(None,[a.get('given',''),a.get('family','')])) for a in row.get('author',[]))
        return dict(Paper_ID=f'ICML{year}_PMLR_{row["id"]}',Title=row['title'],Authors=authors,Abstract=row.get('abstract',''),Venue='ICML',Year=year,Track='Main Conference',Official_URL=row['URL'],PDF_URL=row.get('PDF',''),DOI=row.get('DOI',''),Native_Publisher_ID=row['id'],BibTeX_or_Publisher_Metadata=json.dumps(row,ensure_ascii=False),Formal_Publication_Evidence=f'PMLR v{volume}; {row["container-title"]}; {row["URL"]}',Metadata_Source=source,Published_Date=row.get('published',''),Issue_Date='',Source_Snapshot_UTC=stamp,Access_Status='PUBLIC_METADATA',Abstract_Source_URL=source)
    def detail(self,html,url,year,volume):
        s=BeautifulSoup(html,'html.parser')
        def meta(n): return [x.get('content','') for x in s.select(f'meta[name="{n}"]')]
        title=meta('citation_title'); abstract=s.select_one('#abstract')
        bib=s.select_one('#bibtex')
        if not title or not abstract or not bib or not re.search(r'year\s*=\s*\{'+str(year)+r'\}',bib.get_text()) or not re.search(r'volume\s*=\s*\{'+str(volume)+r'\}',bib.get_text()):
            raise ValueError('Incomplete detail publication evidence')
        return dict(title=title[0],abstract=abstract.get_text(' ',strip=True),authors=meta('citation_author'),bibtex=bib.get_text())

def independent_program(data,venue,year):
    if data.get('next') or len(data.get('results',[]))!=data.get('count'): raise ValueError('Incomplete program pagination')
    target=[]; non_target=[]
    for row in data['results']:
        source=f'https://openreview.net/group?id={venue}.cc/{year}/Conference'
        allowed={source}
        if venue=='ICML': allowed.add(f'https://openreview.net/group?id=ICML.cc/{year}/Position_Paper_Track')
        if row.get('sourceurl') not in allowed or not str(row.get('decision','')).lower().startswith('accept'):
            non_target.append(row); continue
        target.append(row)
    return target,non_target
