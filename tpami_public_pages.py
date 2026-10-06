"""Public CSDL rendered HTML, never backing REST or guessed issue inventories."""
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from tpami_policy import TITLE, normalize_date


def csdl_annual_url(year):
    """Observed public archive route; its final segment selects the year."""
    return f'https://www.computer.org/csdl/journal/tp/past-issues/{year // 10 * 10}/{year}'


def csdl_directory(html, url, year):
    p=urlparse(url)
    if p.hostname!='www.computer.org' or p.path!=urlparse(csdl_annual_url(year)).path:
        return None
    soup=BeautifulSoup(html,'html.parser')
    journal_nav=soup.find('nav',attrs={'aria-label':'Periodical Navigation Menu for '+TITLE})
    if TITLE not in soup.get_text(' ',strip=True) and journal_nav is None:
        raise ValueError('CSDL annual page lacks TPAMI identity (possibly SPA shell)')
    rows=[];seen=set()
    for a in soup.select('#pastIssuesMenu a.cover-image-link[href]'):
        link=urljoin(url,a['href']);path=urlparse(link)
        identity=re.fullmatch(r'/csdl/journal/tp/(\d{4})/(\d+)',path.path)
        if not identity or path.hostname!=p.hostname or path.scheme!='https' or int(identity[1])!=year:
            continue
        label=re.fullmatch(r'Year (\d{4}), Issue Number (\d+)',a.get('aria-label',''))
        panel=a.find_parent(class_='past-issue-panel')
        heading=panel.find('h3') if panel else None
        date=normalize_date(heading.get_text(' ',strip=True).replace('.','')) if heading else ''
        if not label or label[1]!=identity[1] or int(label[2])!=int(identity[2]) or not date.startswith(str(year)):
            raise ValueError('CSDL issue URL, label and publication date disagree')
        key=int(identity[2])
        if key<1 or key in seen:raise ValueError('Invalid/duplicate CSDL issue identity')
        seen.add(key)
        # Volume is read on the issue page, never derived from year arithmetic.
        rows.append(dict(Year=year,Volume='',Issue=str(key),Issue_Publication_Date=date,
            Issue_URL=link,Publication_Number='34',Directory_Source_URL=url,
            Issue_Year_Evidence='Official archive URL, aria-label and month heading'))
    if not rows:raise ValueError('CSDL rendered annual issue links unavailable; no inferred inventory')
    return rows


def csdl_issue_page(html,url,context):
    p=urlparse(url)
    if p.hostname!='www.computer.org':return None
    soup=BeautifulSoup(html,'html.parser')
    if not soup.select('.article-title[href]'):return None
    expected=urlparse(context['Issue_URL'])
    identity=re.fullmatch(r'/csdl/journal/tp/(\d{4})/(\d+)',p.path)
    if (not identity or p.path!=expected.path or p.scheme!='https'
            or int(identity[1])!=int(context['Year']) or int(identity[2])!=int(context['Issue'])):
        raise ValueError('CSDL issue URL/year/issue mismatch')
    text=soup.get_text(' ',strip=True)
    volume=re.search(r'Volume\s*:?\s*(\d+)\s*[,·]?\s*Issue\s*:?\s*(\d+)',text,re.I)
    if TITLE not in text or not volume or int(volume[2])!=int(context['Issue']):
        raise ValueError('CSDL issue page lacks explicit matching journal/volume/issue')
    if context.get('Volume') and str(context['Volume'])!=volume[1]:
        raise ValueError('CSDL volume conflicts with annual context')
    context={**context,'Volume':volume[1]}
    rows=[];seen=set()
    for a in soup.select('.article-title[href]'):
        link=urljoin(url,a['href']);path=urlparse(link)
        match=re.fullmatch(r'/csdl/journal/tp/(\d{4})/(\d+)/(\d+)/([^/]+)',path.path)
        if not match or path.hostname!=expected.hostname or path.scheme!='https':continue
        if int(match[1])!=int(context['Year']) or int(match[2])!=int(context['Issue']):continue
        if match[3] in seen:raise ValueError('Duplicate document in CSDL issue page')
        seen.add(match[3])
        authors=[n.get_text(' ',strip=True) for n in a.parent.select('.article-authors a')]
        title=a.get_text(' ',strip=True)
        if not title or not authors or not all(authors):raise ValueError('CSDL article title/authors incomplete')
        rows.append(dict(Title=title,Authors='; '.join(authors),Native_Publisher_ID=match[3],
            Official_URL='https://ieeexplore.ieee.org/document/'+match[3],
            Independent_Source_URL=link,Retrieval_Source=url,**context))
    count=re.search(r'Showing\s+(\d+)\s+out of\s+(\d+)',text,re.I)
    if not rows or not count or int(count[1])!=len(rows):
        raise ValueError('CSDL visible article count missing or inconsistent')
    # Partial DOM remains partial. Do not manufacture load-more REST endpoints.
    return dict(rows=rows,declared_count=int(count[2]),pages=[],context=context)
