"""Bounded live smoke: at most six distinct URLs and one detail per platform.

Never runs a year pipeline or writes to a library. No PDF requests.
"""
from pathlib import Path
import argparse
import json
import traceback

from venue_runtime import FetchCache, write_json, write_csv, utc, job_lock
from venue_pipelines import springer_books, VOLUMES, conference_program, aaai_issue_links, ecva_index
from venue_adapters import PMLRAdapter
from iclr_adapter import ICLRAdapter
from ojs_adapter import AAAIOJSAdapter
from springer_adapter import SpringerECCVAdapter
from venue_audit import validate_rows


class BoundedCache(FetchCache):
    def get(self, url):
        if self.requests >= 6:
            raise RuntimeError('Smoke request budget exhausted')
        return super().get(url)


def smoke(venue, root):
    year = {'ICML': 2025, 'ICLR': 2025, 'AAAI': 2026, 'ECCV': 2024}[venue]
    base = root / venue / str(year)
    result = dict(venue=venue, year=year, utc=utc(), status='FAILED', detail_limit=1,
                  rate_limit_note='Observe only; no deliberate 429 load generation')
    with job_lock(base, venue, year):
        c = BoundedCache(base, venue, year)
        try:
            if venue == 'ICML':
                a = PMLRAdapter()
                url = f'https://proceedings.mlr.press/v{VOLUMES[year]}/'
                index = a.index(c.get(url), url, year)
                r = index[0]
                d = a.detail(c.get(r['Official_URL']), r['Official_URL'], year, VOLUMES[year])
                detail = dict(Paper_ID=f'ICML{year}_' + r['Official_URL'].rsplit('/', 1)[-1],
                              Title=d['title'], Authors='; '.join(d['authors']), Abstract=d['abstract'],
                              Venue=venue, Year=year, Track='Main Conference', Official_URL=r['Official_URL'],
                              PDF_URL='', Formal_Publication_Evidence=d['bibtex'])
            elif venue == 'ICLR':
                a = ICLRAdapter()
                url = f'https://proceedings.iclr.cc/paper_files/paper/{year}'
                index, excluded = a.index(c.get(url), url, year)
                r = index[0]
                detail = a.detail(c.get(r['Official_URL']), r['Official_URL'], year, utc())
            elif venue == 'AAAI':
                a = AAAIOJSAdapter()
                url = 'https://ojs.aaai.org/index.php/AAAI/issue/view/683'
                index = a.index(c.get(url), url, year, 40, 1)
                r = next(r for r in index if r['Scope'] == 'TARGET_MAIN')
                detail = a.detail(c.get(r['Official_URL']), r['Official_URL'], year, 40, 1, utc())
            else:
                a = SpringerECCVAdapter()
                url = 'https://link.springer.com/conference/eccv'
                books = springer_books(c.get(url), url, year)
                if not books:
                    raise ValueError('No target Springer books parsed')
                url = books[0]
                book = a.book(c.get(url), url, year)
                index = book['Chapters']
                r = index[0]
                detail = a.detail(c.get(r['Official_URL']), r['Official_URL'], year, book['Book_DOI'], utc())
            write_csv(base / 'index.csv', index)
            write_csv(base / 'detail.csv', [detail])
            problems = validate_rows([detail], venue, year)
            if problems:
                raise ValueError(str(problems))
            result.update(status='PASSED', index_count=len(index), detail_count=1,
                          note='Entry/index/detail only; not annual completeness or production qualification')
            # Bounded discovery probes only: no annual detail crawl or OAI pagination.
            try:
                if venue in ('ICML', 'ICLR'):
                    program, excluded = conference_program(c, venue, year)
                    probe = dict(status='PASSED', target_event_count=len(program), excluded_count=len(excluded))
                elif venue == 'AAAI':
                    mirror = f'https://aaai.org/proceeding/aaai-{year - 1986}-{year}/'
                    issues = aaai_issue_links(c.get(mirror), mirror, year)
                    if not issues:
                        raise ValueError('Official annual mirror has no parsed target issue links')
                    import xml.etree.ElementTree as ET
                    tree = ET.fromstring(c.get('https://ojs.aaai.org/index.php/AAAI/oai?verb=ListRecords&metadataPrefix=oai_dc'))
                    records = tree.findall('.//{http://www.openarchives.org/OAI/2.0/}record')
                    if not records:
                        raise ValueError('OAI first page has no records')
                    probe = dict(status='PASSED', issue_count=len(issues), oai_first_page_records=len(records),
                                 note='Only first OAI page; annual coverage not established')
                else:
                    program = ecva_index(c.get('https://www.ecva.net/papers.php'), year)
                    probe = dict(status='PASSED', ecva_count=len(program),
                                 conference_book_count=len(books), first_book_declared=book['Declared_Chapter_Count'],
                                 first_book_page_chapters=len(index), other_volume_count=len(book['Other_Volumes']))
                result['independent_probe'] = probe
            except Exception as exc:
                result['independent_probe'] = dict(status='FAILED', error=repr(exc))
        except Exception as exc:
            result.update(error=repr(exc), traceback=traceback.format_exc())
        finally:
            result['requests'] = c.requests
            result['cache_hits'] = c.hits
            result['sources'] = c.manifest()
            write_json(base / 'Smoke_Result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venue', nargs='+', choices=['ICML', 'ICLR', 'AAAI', 'ECCV'], default=['ICML', 'ICLR', 'AAAI', 'ECCV'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent / 'output' / 'smoke'
    for venue in args.venue:
        result = smoke(venue, root)
        print(json.dumps({k: v for k, v in result.items() if k not in ('traceback', 'sources')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
