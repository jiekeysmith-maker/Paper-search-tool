"""Public IEEE/Xplore + Computer Society final-issue audit pipeline.

No /rest endpoints, authentication tricks, search-result completeness claims,
or API-key emulation. If public pages are SPA shells, the year stays unverified.
"""
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
from venue_runtime import write_json
from tpami_adapter import TPAMIAdapter
from tpami_policy import YEAR_BASIS


def tpami(cache, collection):
    year, adapter = cache.year, TPAMIAdapter()
    sources = [('publisher', 'https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=34'),
               ('independent', f'https://www.computer.org/csdl/journal/tp/{year}')]
    inventories, statuses, permissions = {}, [], {}
    def get_public(url):
        host=urlparse(url).netloc
        if host not in permissions:
            robots=RobotFileParser()
            robots.parse(cache.get(f'https://{host}/robots.txt').splitlines())
            permissions[host]=robots
        if not permissions[host].can_fetch('PaperSearchTool',url):
            raise ValueError('ROBOTS_DISALLOWED: official source not requested')
        return cache.get(url)
    for role, url in sources:
        host = urlparse(url).netloc
        try:
            rows = adapter.volume_directory(get_public(url), url, year)
            inventories[role] = rows
            statuses.append(dict(role=role, url=url, status='ENUMERATED', issues=len(rows)))
        except Exception as exc:
            collection.issue(f'{role} annual source unavailable: {exc}')
            statuses.append(dict(role=role, url=url, status='UNAVAILABLE', issues=None, error=repr(exc)))
    write_json(cache.base / 'raw/IEEE_Source_Enumeration.json', dict(year=year, year_basis=YEAR_BASIS,
               sources=statuses, inventories=inventories, annual_article_count=None))
    if len(inventories) != 2:
        collection.save()
        return
    first = {(r['Volume'], r['Issue']) for r in inventories['publisher']}
    second = {(r['Volume'], r['Issue']) for r in inventories['independent']}
    if first != second or {int(r['Issue']) for r in inventories['publisher']} != set(range(1, 13)):
        collection.issue('IEEE final-year issue inventories disagree or do not cover twelve monthly issues')
    for role, contexts in inventories.items():
        target = collection.publisher if role == 'publisher' else collection.program
        for context in sorted(contexts, key=lambda r: int(r['Issue'])):
            target.extend(adapter.issue_index(get_public(context['Issue_URL']), context['Issue_URL'], context))
            collection.save()
    for row in collection.publisher:
        collection.corpus.append(adapter.detail(get_public(row['Official_URL']),row['Official_URL'],row))
        collection.save()
    collection.evidence_complete = first == second and {int(r['Issue']) for r in inventories['publisher']} == set(range(1, 13))
    write_json(cache.base / 'raw/IEEE_Source_Enumeration.json', dict(year=year, year_basis=YEAR_BASIS,
               sources=statuses, inventories=inventories, annual_article_count=len(collection.publisher)))
