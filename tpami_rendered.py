"""Opt-in foreground public rendering transport for TPAMI.

Uses a fresh, unauthenticated browser, normal page navigation and visible year
controls only. No REST calls, cookies from user profiles, stealth or auth retries.
FetchCache still owns persistence, pacing, hashes, manifests and manual reuse.
The optional Playwright dependency is never installed or started on import.
"""
import re
import json
from pathlib import Path
from hashlib import sha256
from urllib.parse import urlparse, parse_qs, urljoin
from urllib.robotparser import RobotFileParser
import requests
from metadata_transport import get_metadata
from venue_runtime import atomic_bytes, write_json, utc


def public_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.username or p.port not in (None,443):return False
    if p.hostname=='ieeexplore.ieee.org':
        if re.fullmatch(r'/document/\d+/?',p.path):return not p.query
        if p.path not in ('/xpl/RecentIssue.jsp','/xpl/issues','/xpl/tocresult.jsp'):return False
        q=parse_qs(p.query)
        return (q.get('punumber')==['34'] and set(q)<={'punumber','isnumber','pageNumber','sortType'}
                and all(len(q[k])==1 and q[k][0].isdigit() for k in ('isnumber','pageNumber') if k in q))
    return p.hostname=='www.computer.org' and bool(re.fullmatch(
        r'/csdl/journal/tp/(?:past-issues/\d{4}/\d{4}|\d{4}/\d+)',p.path)) and not p.query


class PublicRenderer:
    def __init__(self,base,year,*,page=None,http=get_metadata):
        self.base=Path(base);self.year=year;self.page=page;self.http=http
        self.browser=None;self.engine=None;self.robots={};self.blocked={};self.navigation=[]
        self.robots_loader=None

    def close(self):
        if self.browser is not None:self.browser.close()
        if self.engine is not None:self.engine.stop()

    def permitted(self,url):
        if not public_url(url):raise ValueError('Non-public navigation forbidden: '+url)
        host=urlparse(url).hostname
        if host in self.blocked:raise ValueError('OFFICIAL_ACCESS_RESTRICTION: '+self.blocked[host])
        if host not in self.robots:
            if self.robots_loader is not None:
                text=self.robots_loader(f'https://{host}/robots.txt')
            else:
                response=self.http(f'https://{host}/robots.txt');response.raise_for_status()
                if response.status_code!=200 or response.url!=f'https://{host}/robots.txt':raise ValueError('Robots permission unavailable')
                text=response.text
            if '<html' in text.lower():raise ValueError('Robots permission unavailable (HTML)')
            parser=RobotFileParser();parser.parse(text.splitlines())
            self.robots[host]=(parser,text)
        parser,text=self.robots[host]
        if not parser.can_fetch('PaperSearchTool',url) or (urlparse(url).query and re.search(r'^Disallow:\s*/\*\?\*\s*$',text,re.M)):
            raise ValueError('ROBOTS_DISALLOWED: '+url)

    def _start(self):
        if self.page is not None:return
        from playwright.sync_api import sync_playwright
        self.engine=sync_playwright().start()
        self.browser=self.engine.chromium.launch(headless=True)
        context=self.browser.new_context(accept_downloads=False,service_workers='block')
        self.page=context.new_page();self.page.set_default_timeout(30000)
        self.page.on('response',self._response)
        def guard(route):
            request=route.request
            host=urlparse(request.url).hostname
            if host in self.blocked:return route.abort()
            if request.is_navigation_request() and request.frame==self.page.main_frame:
                try:self.permitted(request.url)
                except Exception as exc:
                    self.blocked[host]=str(exc);return route.abort()
            return route.continue_()
        self.page.route('**/*',guard)

    def _response(self,response):
        host=urlparse(response.url).hostname
        if host in ('ieeexplore.ieee.org','www.computer.org') and response.status in (401,403,429):
            self.blocked[host]=f'HTTP {response.status}: {response.url}'

    def _goto(self,url):
        self.permitted(url)
        response=self.page.goto(url,wait_until='domcontentloaded',timeout=30000)
        self.permitted(self.page.url)
        if response is None or response.status!=200:
            status=response.status if response else None
            if status in (202,401,403,429):self.blocked[urlparse(url).hostname]=f'HTTP {status}'
            raise ValueError(f'OFFICIAL_ACCESS_RESTRICTION: navigation did not return 200 ({status})')
        raw=response.body();digest=sha256(raw).hexdigest()
        path=self.base/'raw/rendered_navigation'/f'{digest}.html'
        atomic_bytes(path,raw)
        self.navigation.append(dict(url=url,final_url=self.page.url,http_status=200,
                                    snapshot=str(path),sha256=digest,utc=utc()))

    def __call__(self,url,*,history=None,**kwargs):
        if urlparse(url).path=='/robots.txt':
            response=self.http(url,history=history,**kwargs)
            response.raise_for_status()
            if response.status_code!=200 or response.url!=url or '<html' in response.text.lower():
                raise ValueError('Robots permission unavailable or redirected')
            parser=RobotFileParser();parser.parse(response.text.splitlines())
            self.robots[urlparse(url).hostname]=(parser,response.text)
            return response
        self.navigation=[]
        try:
            self.permitted(url)
            self._start()
            self._goto(url)
            p=urlparse(url)
            if p.path=='/xpl/RecentIssue.jsp':
                link=self.page.get_by_role('link',name='All Issues',exact=True)
                link.wait_for(state='visible')
                self._goto(urljoin(self.page.url,link.get_attribute('href')))
                self.page.locator('.issue-details a[href]').first.wait_for(state='visible')
                selected=self.page.locator('li.active a[data-analytics_identifier="past_issue_selected_year"]').inner_text().strip()
                if selected!=str(self.year):
                    old=self.page.locator('.issue-details a[href]').first.get_attribute('href')
                    self.page.locator('a[data-analytics_identifier="past_issue_selected_year"]').filter(has_text=re.compile(r'^'+str(self.year)+r'$')).click()
                    self.page.wait_for_function("old => { const a=document.querySelector('.issue-details a[href]'); return a && a.getAttribute('href')!==old; }",arg=old)
            elif '/past-issues/' in p.path:
                self.page.locator('#pastIssuesMenu .cover-image-link').first.wait_for(state='visible')
            elif p.hostname=='www.computer.org':
                self.page.locator('.article-title[href]').first.wait_for(state='visible')
            elif p.path=='/xpl/tocresult.jsp':
                self.page.locator('.result-item-align h2 a[href]').first.wait_for(state='visible')
            else:
                self.page.get_by_role('heading',name='Abstract:',exact=True).wait_for(state='visible')
            self.permitted(self.page.url)
            title=self.page.title()
            if re.search(r'access denied|authentication|verify you are human|just a moment|sign in|log in',title,re.I):
                raise ValueError('OFFICIAL_ACCESS_RESTRICTION: '+title)
            html=self.page.content()
            capture=dict(kind='PUBLIC_RENDERED_HTML',year=self.year,requested_url=url,
                final_url=self.page.url,utc=utc(),dom_sha256=sha256(html.encode('utf-8')).hexdigest())
            # Acquisition evidence, not a fabricated publisher count. The audit
            # still has to validate the selected year and every discovered issue.
            html+='\n<script type="application/json" id="tpami-rendered-capture">'+json.dumps(capture).replace('<','\\u003c')+'</script>'
            result=requests.Response();result.status_code=200;result.url=self.page.url
            result._content=html.encode('utf-8');result.headers['Content-Type']='text/html; charset=utf-8'
            if history is not None:history.append(dict(kind='PUBLIC_RENDERED_HTML',year=self.year,
                requested_url=url,final_url=self.page.url,navigation=self.navigation,utc=utc()))
            return result
        except Exception as exc:
            write_json(self.base/'raw/Rendered_Access_Status.json',dict(url=url,error=repr(exc),
                blocked=self.blocked,navigation=self.navigation,utc=utc()))
            raise
