"""Public CSDL rendered HTML, never backing REST or guessed issue inventories."""
import re
import json
from hashlib import sha256
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from bs4 import BeautifulSoup
from tpami_policy import TITLE, normalize_date


def rendered_inventory_evidence(html,rows):
    """Closure of the public selected-year UI, cross-checked by the pipeline.

    This witnesses the captured directory, not future publication or availability.
    Missing capture provenance, pending loading or pagination always fails closed.
    """
    soup=BeautifulSoup(html,'html.parser');marker=soup.find('script',id='tpami-rendered-capture')
    if marker is None:return None
    result=dict(complete=False,basis='PUBLIC_RENDERED_YEAR_DIRECTORY',discovered_issue_count=len(rows))
    try:
        proof=json.loads(marker.get_text())
        original=html.rsplit('\n<script type="application/json" id="tpami-rendered-capture">',1)[0]
        if (proof.get('kind')!='PUBLIC_RENDERED_HTML' or proof.get('dom_sha256')!=sha256(original.encode()).hexdigest()
                or not rows or {str(r['Year']) for r in rows}!={str(proof.get('year'))}
                or {r.get('Directory_Source_URL') for r in rows}!={proof.get('requested_url')}):return result
        p=urlparse(proof['final_url']);year=str(proof['year'])
        if p.hostname=='www.computer.org':
            scope=soup.select_one('#pastIssuesMenu')
            selected=soup.select('#pastIssuesMenu li.active a[aria-label="Select Year '+year+'"]')
            if not scope or len(selected)!=1 or p.path!=urlparse(csdl_annual_url(int(year))).path:return result
            entries=scope.select('.cover-image-link[href]')
        elif p.hostname=='ieeexplore.ieee.org' and p.path=='/xpl/issues':
            scope=soup.select_one('main') or soup
            selected=soup.select('li.active a[data-analytics_identifier="past_issue_selected_year"]')
            if [x.get_text(strip=True) for x in selected]!=[year]:return result
            entries=soup.select('.issue-details a[href]')
        else:return result
        text=scope.get_text(' ',strip=True)
        if (len(entries)!=len(rows) or scope.select('.pagination, [rel="next"], [aria-busy="true"]')
                or re.search(r'Getting results|Loading|Load more',text,re.I)):return result
        result.update(complete=True,captured_at=proof['utc'],final_url=proof['final_url'],
                      dom_sha256=proof['dom_sha256'],note='All displayed final issues at capture time; independent inventory must also agree')
    except (ValueError,KeyError,TypeError):pass
    return result


def csdl_annual_url(year):
    """Observed public archive route; its final segment selects the year."""
    return f'https://www.computer.org/csdl/journal/tp/past-issues/{year // 10 * 10}/{year}'


def ieee_directory(html,url,year):
    if urlparse(url).hostname!='ieeexplore.ieee.org':return None
    soup=BeautifulSoup(html,'html.parser')
    links=soup.select('.issue-details a[href]')
    if not links:return None
    selected=soup.select('li.active a[data-analytics_identifier="past_issue_selected_year"]')
    if TITLE not in soup.get_text(' ',strip=True) or [x.get_text(strip=True) for x in selected]!=[str(year)]:
        raise ValueError('IEEE rendered directory selected year/journal mismatch')
    volumes={m[1] for el in soup.select('strong') if (m:=re.fullmatch(r'Volume\s+(\d+)',el.get_text(' ',strip=True)))}
    if len(volumes)!=1:raise ValueError('IEEE rendered annual volume absent/ambiguous')
    volume=volumes.pop();rows=[];ids=set();numbers=set()
    for a in links:
        label=re.fullmatch(r'Issue\s+(\d+)',a.get_text(' ',strip=True))
        link=urljoin(url,a['href']);p=urlparse(link);query=parse_qs(p.query)
        native=query.get('isnumber',[])
        if (not label or p.scheme!='https' or p.hostname!='ieeexplore.ieee.org'
                or p.path!='/xpl/tocresult.jsp' or query.get('punumber')!=['34']
                or len(native)!=1 or not native[0].isdigit()):raise ValueError('IEEE invalid issue link')
        if native[0] in ids or label[1] in numbers:raise ValueError('Duplicate IEEE annual issue identity')
        ids.add(native[0]);numbers.add(label[1])
        rows.append(dict(Year=year,Volume=volume,Issue=label[1],Issue_Publication_Date='',
            Issue_URL=link,Publication_Number='34',Directory_Source_URL=url,
            Issue_Year_Evidence='Selected official year and volume; date pending issue page'))
    return rows


def ieee_issue_page(html,url,context):
    p=urlparse(url)
    if p.hostname!='ieeexplore.ieee.org':return None
    soup=BeautifulSoup(html,'html.parser');containers=soup.select('.result-item-align')
    if not containers:return None
    text=soup.get_text(' ',strip=True)
    label=re.search(r'Issue\s+(\d+)\s*•\s*([A-Za-z]+)\.?-(\d{4})',text)
    if (TITLE not in text or not label or label[1]!=str(context['Issue'])
            or label[3]!=str(context['Year']) or not context.get('Volume')
            or parse_qs(p.query).get('isnumber')!=parse_qs(urlparse(context['Issue_URL']).query).get('isnumber')):
        raise ValueError('IEEE rendered issue context mismatch')
    date=normalize_date(label[2]+' '+label[3])
    if not date or (context.get('Issue_Publication_Date') and context['Issue_Publication_Date']!=date):
        raise ValueError('IEEE issue publication date mismatch')
    context={**context,'Issue_Publication_Date':date};rows=[];seen=set()
    for container in containers:
        a=container.select_one('h2 a[href]')
        if a is None:raise ValueError('IEEE result lacks article heading')
        link=urljoin(url,a['href']);identity=re.fullmatch(r'https://ieeexplore\.ieee\.org/document/(\d+)/?',link)
        if not identity or identity[1] in seen:raise ValueError('Invalid/duplicate IEEE result identity')
        seen.add(identity[1])
        authors=[n.get_text(' ',strip=True) for n in container.select('xpl-authors-name-list a[href^="/author/"]')]
        title=a.get_text(' ',strip=True)
        if not title or not authors:raise ValueError('IEEE result title/authors missing')
        rows.append(dict(Title=title,Authors='; '.join(authors),Native_Publisher_ID=identity[1],
            Official_URL=f'https://ieeexplore.ieee.org/document/{identity[1]}',**context))
    count=re.search(r'Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)',text)
    if not count or int(count[2])-int(count[1])+1!=len(rows) or int(count[2])>int(count[3]):
        raise ValueError('IEEE rendered page range/count inconsistent')
    pages=[]
    next_button=soup.find('button',attrs={'aria-label':'Next page of search results'})
    if int(count[2])<int(count[3]) and next_button and not next_button.has_attr('disabled'):
        query=parse_qs(p.query);page=int(query.get('pageNumber',['1'])[0])
        query.update(pageNumber=[str(page+1)],sortType=['vol-only-seq'])
        # Public URL observed after clicking Next in the normal issue UI.
        pages.append(urlunparse(p._replace(query=urlencode(query,doseq=True))))
    return dict(rows=rows,declared_count=int(count[3]),pages=pages,context=context)


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
