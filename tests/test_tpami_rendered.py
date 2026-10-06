"""Offline renderer transport contracts; never starts a browser during tests."""
import json
from hashlib import sha256
from urllib.robotparser import RobotFileParser
import pytest
import requests
from tpami_rendered import PublicRenderer,public_url
from tpami_adapter import TPAMIAdapter
from tpami_policy import TITLE
from venue_runtime import FetchCache


def response(url,status=200,body='<app-root/>'):
    r=requests.Response();r.url=url;r.status_code=status;r._content=body.encode();return r


class Page:
    def __init__(self,status=200,redirect=None,title='TPAMI'):
        self.status=status;self.redirect=redirect;self.label=title;self.calls=[]
    def goto(self,url,**kwargs):
        self.calls.append(url);self.url=self.redirect or url
        r=response(self.url,self.status);r.status=self.status;r.body=lambda:b'<app-root/>'
        return r
    def get_by_role(self,*args,**kwargs):return self
    def wait_for(self,**kwargs):pass
    def title(self):return self.label
    def content(self):return '<h2>Abstract:</h2><p>Public metadata only.</p>'


def renderer(tmp_path,page):
    r=PublicRenderer(tmp_path,2024,page=page,http=lambda u,**k:response(u,body='User-agent: *\nAllow: /'))
    return r


def test_rendered_cache_replay_keeps_navigation_provenance(tmp_path):
    page=Page();r=renderer(tmp_path,page);cache=FetchCache(tmp_path,'TPAMI',2024,fetch=r,delay=0)
    url='https://ieeexplore.ieee.org/document/123'
    assert 'Public metadata' in cache.get(url)
    assert cache.get(url,cache_only=True)==cache.get(url) and len(page.calls)==1
    meta=json.loads(cache.paths(url)[1].read_text())
    proof=meta['history'][0]
    assert proof['kind']=='PUBLIC_RENDERED_HTML' and proof['year']==2024
    assert proof['navigation'][0]['sha256']


@pytest.mark.parametrize('status',[202,401,403,429,500])
def test_denied_response_not_success_cache(tmp_path,status):
    r=renderer(tmp_path,Page(status));cache=FetchCache(tmp_path,'TPAMI',2024,fetch=r,delay=0)
    url='https://ieeexplore.ieee.org/document/123'
    with pytest.raises(ValueError):cache.get(url)
    assert not cache.has_snapshot(url)
    assert (tmp_path/'raw/Rendered_Access_Status.json').is_file()


def test_auth_redirect_and_robots_never_become_metadata(tmp_path):
    r=renderer(tmp_path,Page(redirect='https://ieeexplore.ieee.org/Xplore/login.jsp'))
    with pytest.raises(ValueError,match='Non-public'):r('https://ieeexplore.ieee.org/document/123')
    page=Page();r=renderer(tmp_path,page)
    robots=RobotFileParser();robots.parse(['User-agent: *','Disallow: /'])
    r.robots['ieeexplore.ieee.org']=(robots,'User-agent: *\nDisallow: /')
    with pytest.raises(ValueError,match='ROBOTS_DISALLOWED'):r('https://ieeexplore.ieee.org/document/123')
    assert page.calls==[]


@pytest.mark.parametrize('url',[
    'https://ieeexplore.ieee.org/rest/document/123',
    'https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=123',
    'https://ieeexplore.ieee.org/xpl/issues?punumber=999',
    'https://www.computer.org/csdl/journal/xx/2024/01',
    'https://evil.example/document/123',
])
def test_public_navigation_scope(url):assert not public_url(url)


def test_ieee_selected_year_and_issue_not_month_assumption():
    html=f'''<h1>{TITLE}</h1><li class="active"><a data-analytics_identifier="past_issue_selected_year">2024</a></li>
        <strong>Volume 46</strong><div class="issue-details"><a href="/xpl/tocresult.jsp?isnumber=777&amp;punumber=34">Issue 13</a></div>'''
    a=TPAMIAdapter();rows=a.volume_directory(html,'https://ieeexplore.ieee.org/xpl/issues?punumber=34',2024)
    assert rows[0]['Issue']=='13' and rows[0]['Issue_Publication_Date']==''
    with pytest.raises(ValueError):a.volume_directory(html,'https://ieeexplore.ieee.org/xpl/issues?punumber=34',2025)


def test_ieee_rendered_next_page_uses_observed_public_route():
    html=f'''<h1>{TITLE}</h1><p>Issue 1 • Jan.-2024</p>Showing 1-1 of 2
        <div class="result-item-align"><h2><a href="/document/123/">Title</a></h2>
        <xpl-authors-name-list><a href="/author/456">Jane Doe</a></xpl-authors-name-list></div>
        <button aria-label="Next page of search results">&gt;</button>'''
    url='https://ieeexplore.ieee.org/xpl/tocresult.jsp?isnumber=777&punumber=34'
    ctx=dict(Year=2024,Volume='46',Issue='1',Issue_Publication_Date='',Issue_URL=url)
    page=TPAMIAdapter().issue_page(html,url,ctx)
    assert page['context']['Issue_Publication_Date']=='2024-01'
    assert page['declared_count']==2 and 'pageNumber=2' in page['pages'][0]
    assert page['rows'][0]['Authors']=='Jane Doe'
    with pytest.raises(ValueError):TPAMIAdapter().issue_page(html.replace('1-1','1-2'),url,ctx)


def test_rendered_inventory_requires_untampered_selected_year_and_no_pagination():
    from test_tpami_public_pages import annual
    from tpami_public_pages import csdl_annual_url
    a=TPAMIAdapter();url=csdl_annual_url(2024)
    html=annual(2024).replace('<div id="pastIssuesMenu">',
        '<div id="pastIssuesMenu"><li class="active"><a aria-label="Select Year 2024">2024</a></li>')
    rows=a.volume_directory(html,url,2024)
    def capture(body,year=2024):
        data=dict(kind='PUBLIC_RENDERED_HTML',year=year,requested_url=url,final_url=url,utc='2026-10-06',dom_sha256=sha256(body.encode()).hexdigest())
        return body+'\n<script type="application/json" id="tpami-rendered-capture">'+json.dumps(data)+'</script>'
    assert a.inventory_evidence(capture(html),rows)['complete']
    assert not a.inventory_evidence(html,rows)['complete']
    assert not a.inventory_evidence(capture(html,2025),rows)['complete']
    assert not a.inventory_evidence(capture(html).replace('Jan.','Feb.'),rows)['complete']
    partial=html.replace('</li>','</li><div class="pagination">Next</div>',1)
    assert not a.inventory_evidence(capture(partial),rows)['complete']
