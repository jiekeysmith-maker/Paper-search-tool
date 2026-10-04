"""ICLR electronic proceedings adapter; publication and program stay distinct."""
import re,json
from urllib.parse import urlparse,urljoin
from bs4 import BeautifulSoup

class ICLRAdapter:
    def identity(self,url,year):
        p=urlparse(url)
        m=re.fullmatch(r'/paper_files/paper/'+str(year)+r'/hash/([a-f0-9]+)-Abstract-Conference.html',p.path)
        if p.hostname!='proceedings.iclr.cc' or not m:raise ValueError('Wrong publisher/year/track')
        return m[1]

    def index(self,html,url,year):
        s=BeautifulSoup(html,'html.parser')
        h=s.select_one('.book-title');count=s.select_one('.paper-count')
        if not h or h.get_text(' ',strip=True)!=f'International Conference on Learning Representations {year}':raise ValueError('Wrong index year/conference')
        if s.select('a[rel="next"], .pagination'):raise ValueError('Pagination requires explicit enumeration')
        items=s.select('.paper-list > li')
        if not count or not items or int(re.search(r'\d+',count.get_text()).group())!=len(items):raise ValueError('Incomplete index')
        target=[];excluded=[]
        for item in items:
            a=item.select_one('.paper-content a[href]');authors=item.select_one('.paper-authors')
            if not a or not authors:raise ValueError('Missing index metadata')
            u=urljoin(url,a['href'])
            row=dict(Title=a.get_text(' ',strip=True),Authors=authors.get_text(' ',strip=True),Official_URL=u,Track=item.get('data-track',''))
            if row['Track']!='conference':excluded.append(row);continue
            row['Native_Publisher_ID']=self.identity(u,year);target.append(row)
        if len({r['Official_URL'] for r in target})!=len(target):raise ValueError('Duplicate publisher URL')
        return target,excluded

    def detail(self,html,url,year,stamp):
        native=self.identity(url,year);s=BeautifulSoup(html,'html.parser')
        def meta(n):return [m.get('content','') for m in s.select(f'meta[name="{n}"]')]
        titles=meta('citation_title');years=meta('citation_volume');pub=meta('citation_journal_title')
        track=s.select_one('.paper-track');author=s.select_one('.paper-authors');abstract=s.select_one('.paper-abstract');parent=s.select_one('.paper-meta a[href]')
        if years!=[str(year)] or pub!=['International Conference on Learning Representations'] or not track or track.get_text(strip=True)!='Conference':raise ValueError('Wrong publication scope')
        if not parent or urljoin(url,parent['href']).rstrip('/')!=f'https://proceedings.iclr.cc/paper_files/paper/{year}':raise ValueError('Wrong parent volume')
        if not titles or not author or not abstract or not abstract.get_text(' ',strip=True):raise ValueError('Incomplete detail metadata')
        metadata={m.get('name'):m.get('content') for m in s.select('meta[name^="citation_"]') if m.get('name')!='citation_author'}
        metadata['citation_author']=meta('citation_author')
        return dict(Paper_ID=f'ICLR{year}_{native}',Title=titles[0],Authors=author.get_text(' ',strip=True),Abstract=abstract.get_text(' ',strip=True),Venue='ICLR',Year=year,Track='Main Conference',Official_URL=url,PDF_URL=(meta('citation_pdf_url') or [''])[0],DOI=(meta('citation_doi') or [''])[0],Native_Publisher_ID=native,BibTeX_or_Publisher_Metadata=json.dumps(metadata,ensure_ascii=False),Formal_Publication_Evidence=f'ICLR official electronic proceedings {year}; Conference; {url}',Metadata_Source=url,Published_Date=(meta('citation_publication_date') or [''])[0],Issue_Date='',Source_Snapshot_UTC=stamp,Access_Status='PUBLIC_METADATA',Abstract_Source_URL=url,Formal_Proof_URL=url)
