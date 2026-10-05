"""Springer ECCV books/chapters; conference year differs from publication date."""
import re,json
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse
from bs4 import BeautifulSoup

class SpringerECCVAdapter:
    def book(self,html,url,year):
        s=BeautifulSoup(html,'html.parser');title=s.select_one('meta[name="title"]');doi=s.select_one('meta[name="doi"]')
        if not title or title['content']!=f'Computer Vision – ECCV {year}' or not doi:raise ValueError('Wrong book conference/year/track')
        book_path=urlparse(url).path
        if not doi['content'].startswith('10.1007/') or (book_path.startswith('/book/10.1007/') and book_path!='/book/'+doi['content']):
            raise ValueError('Book DOI/canonical identity mismatch')
        text=s.get_text(' ',strip=True)
        part=re.search(r'Proceedings,\s+Part\s+([IVXLCDM]+)',text)
        lncs=re.search(r'LNCS, volume (\d+)',text)
        count=s.select_one('#toc')
        count=re.search(r'Table of contents \((\d+) papers\)',count.get_text(' ',strip=True)) if count else None
        if not part or not lncs or not count:raise ValueError('Incomplete book metadata')
        chapters=[];non_chapters=[]
        for a in s.select('a[data-track="click_book_toc"]'):
            u=urljoin(url,a['href'])
            if '/chapter/' not in u:
                non_chapters.append(dict(Title=a.get_text(' ',strip=True),Official_URL=u,Book_DOI=doi['content'],Evidence_URL=url))
                continue
            if not u.startswith('https://link.springer.com/chapter/'+doi['content']+'_'):raise ValueError('Chapter outside parent book')
            container=a.parent.parent
            authors=container.select_one('.app-author-list') if container else None
            chapters.append(dict(Title=a.get_text(' ',strip=True),Authors=authors.get_text(' ',strip=True) if authors else '',Official_URL=u,Book_DOI=doi['content']))
        if not chapters or len({r['Official_URL'] for r in chapters})!=len(chapters):raise ValueError('Empty/duplicate table of contents')
        pages=set()
        for a in s.select('a[href]'):
            p=urlparse(urljoin(url,a['href']))
            q=parse_qs(p.query)
            if (p.scheme=='https' and p.hostname=='link.springer.com'
                    and p.path in ('/book/'+doi['content'],book_path) and set(q)=={'page'}
                    and len(q['page'])==1 and q['page'][0].isdigit() and int(q['page'][0])>=1):
                pages.add(urlunparse(p._replace(fragment='')))
        pages=sorted(pages)
        links=[urljoin(url,a['href']) for a in s.select('.c-book-other-volumes__item a') if a.get_text(' ',strip=True)==title['content']]
        volumes=[u for u in links if urlparse(u).scheme=='https' and urlparse(u).hostname=='link.springer.com' and urlparse(u).path.startswith('/book/')]
        return dict(Book_DOI=doi['content'],Part=part[1],LNCS_Volume=lncs[1],Declared_Chapter_Count=int(count[1]),Chapters=chapters,Pages=pages,Other_Volumes=volumes,
                    Non_Chapter_Entries=non_chapters,Untrusted_Volume_Links=[u for u in links if u not in volumes])

    def detail(self,html,url,year,bookdoi,stamp):
        if not url.startswith('https://link.springer.com/chapter/'+bookdoi+'_'):raise ValueError('Wrong chapter identity')
        s=BeautifulSoup(html,'html.parser')
        def ms(n):return [x.get('content','') for x in s.select(f'meta[name="{n}"]')]
        def m(n):return (ms(n) or [''])[0]
        doi=m('citation_doi');abstract=s.select_one('#Abs1-content')
        if m('citation_inbook_title')!=f'Computer Vision – ECCV {year}' or m('citation_conference_abbrev')!='ECCV':raise ValueError('Wrong conference/year/workshop')
        if doi!=url.split('/chapter/')[1] or m('citation_abstract_html_url')!=url:raise ValueError('DOI/canonical URL mismatch')
        if not abstract or not abstract.get_text(' ',strip=True) or not ms('citation_author'):raise ValueError('Missing abstract/authors')
        if not m('citation_firstpage') or not m('citation_publication_date'):raise ValueError('Missing publication evidence')
        metadata={x.get('name'):x.get('content') for x in s.select('meta[name^="citation_"]') if x.get('name') not in ['citation_author','citation_author_email','citation_author_institution']};metadata['citation_author']=ms('citation_author')
        # Each citation_author is one complete person, commonly "Family, Given".
        # Keep the original values in publisher metadata; normalize display order only.
        authors = [' '.join(reversed(name.split(', ', 1))) if ', ' in name else name for name in ms('citation_author')]
        return dict(Paper_ID=f'ECCV{year}_'+doi.replace('/','_'),Title=m('citation_title'),Authors='; '.join(authors),Abstract=abstract.get_text(' ',strip=True),Venue='ECCV',Year=year,Track='Main Conference',Official_URL=url,PDF_URL=m('citation_pdf_url'),DOI=doi,Native_Publisher_ID=doi,BibTeX_or_Publisher_Metadata=json.dumps(metadata,ensure_ascii=False),Formal_Publication_Evidence=f'Springer ECCV {year}; Book {bookdoi}; Chapter {doi}',Metadata_Source=url,Published_Date=m('citation_publication_date'),Issue_Date='',Source_Snapshot_UTC=stamp,Access_Status='PUBLIC_METADATA',Abstract_Source_URL=url,Formal_Proof_URL=url,Book_DOI=bookdoi)
