"""AAAI OJS metadata adapter with article-level section boundaries."""
import json,re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

class AAAIOJSAdapter:
    def classify_scope(self,section):
        if section.startswith('AAAI Technical Track'):return 'TARGET_MAIN'
        if section.startswith('AAAI Special Track') and ('Social Impact' in section or 'Alignment' in section):return 'TARGET_MAIN'
        if any(x in section for x in ['IAAI','EAAI','Student Abstract','Undergraduate','Demonstration','Doctoral','Senior Member','New Faculty','Journal Track','Emerging Trends','Workshop','Bridge']):return 'NON_TARGET'
        return 'UNCERTAIN'

    def index(self,html,url,year,volume,issue):
        soup=BeautifulSoup(html,'html.parser');head=soup.select_one('h1')
        if not head or f'Vol. {volume} No. {issue}:' not in head.get_text(' ',strip=True) or f'-{str(year)[-2:]}' not in head.get_text():raise ValueError('Wrong issue/year')
        if soup.select('.cmp_pagination a.next'):raise ValueError('Issue pagination not fully enumerated')
        rows=[]
        for section in soup.select('.section'):
            h=section.find('h2',recursive=False)
            if not h:continue
            track=h.get_text(' ',strip=True)
            scope=self.classify_scope(track)
            # The official 2024 issue introduction explicitly names this technical
            # track, while its section heading uses the abbreviated spelling.
            scope_evidence=''
            if (year==2024 and str(volume)=='38' and str(issue)=='9'
                    and track=='Intelligent Robots (ROB)'
                    and 'AAAI Technical Track on Intelligent Robots' in soup.get_text(' ',strip=True)):
                scope='TARGET_MAIN'
                scope_evidence=url+'; issue introduction: AAAI Technical Track on Intelligent Robots'
            for item in section.select('.obj_article_summary'):
                a=item.select_one('h3.title a');authors=item.select_one('.authors')
                if not a or not authors:raise ValueError('Missing article identity')
                native=self.identity(a['href'])
                rows.append(dict(Title=a.get_text(' ',strip=True),Authors=authors.get_text(' ',strip=True),Official_URL=a['href'],Native_Publisher_ID=native,Track=track,Scope=scope,Scope_Evidence=scope_evidence,Issue_URL=url,Volume=str(volume),Issue=str(issue)))
        if not rows or len({r['Official_URL'] for r in rows})!=len(rows):raise ValueError('Empty or duplicated issue')
        # Independent count catches article summaries not assigned to any parsed section.
        if len(rows)!=len(soup.select('.obj_article_summary')):raise ValueError('Unparsed section')
        return rows

    def identity(self,url):
        p=urlparse(url);m=re.fullmatch(r'/index.php/AAAI/article/view/(\d+)',p.path)
        if p.hostname!='ojs.aaai.org' or not m:raise ValueError('Wrong publisher/article URL')
        return m[1]

    def detail(self,html,url,year,volume,issue,stamp):
        native=self.identity(url);s=BeautifulSoup(html,'html.parser')
        def metas(n):return [x.get('content','') for x in s.select(f'meta[name="{n}"]')]
        def meta(n):return (metas(n) or [''])[0]
        fields={x.select_one('.label').get_text(strip=True):x.select_one('.value').get_text(' ',strip=True) for x in s.select('.item.issue .sub_item') if x.select_one('.label') and x.select_one('.value')}
        section=fields.get('Section','');scope=self.classify_scope(section)
        if scope!='TARGET_MAIN':raise ValueError('Non-target or uncertain article section')
        if meta('citation_volume')!=str(volume) or meta('citation_issue')!=str(issue) or not meta('citation_date').startswith(str(year)):raise ValueError('Wrong year/volume/issue')
        doi=meta('citation_doi')
        if doi!=f'10.1609/aaai.v{volume}i{issue}.{native}':raise ValueError('DOI identity mismatch')
        if meta('citation_journal_title')!='Proceedings of the AAAI Conference on Artificial Intelligence':raise ValueError('Wrong journal')
        abstract=s.select_one('.item.abstract')
        if abstract:
            for label in abstract.select('.label'):label.decompose()
        if not abstract or not abstract.get_text(' ',strip=True) or not metas('citation_author'):raise ValueError('Missing abstract/authors')
        if meta('citation_abstract_html_url')!=url:raise ValueError('Canonical URL mismatch')
        metadata={x.get('name'):x.get('content') for x in s.select('meta[name^="citation_"]') if x.get('name') not in ['citation_author','citation_author_institution']};metadata['citation_author']=metas('citation_author')
        return dict(Paper_ID=f'AAAI{year}_OJS_{native}',Title=meta('citation_title'),Authors='; '.join(metas('citation_author')),Abstract=abstract.get_text(' ',strip=True),Venue='AAAI',Year=year,Track=section,Official_URL=url,PDF_URL=meta('citation_pdf_url'),DOI=doi,Native_Publisher_ID=native,BibTeX_or_Publisher_Metadata=json.dumps(metadata,ensure_ascii=False),Formal_Publication_Evidence=f'AAAI Press OJS; Vol {volume} Issue {issue}; {section}; DOI {doi}',Metadata_Source=url,Published_Date=meta('citation_date'),Issue_Date=meta('citation_date'),Source_Snapshot_UTC=stamp,Access_Status='PUBLIC_METADATA',Abstract_Source_URL=url,Formal_Proof_URL=url)
