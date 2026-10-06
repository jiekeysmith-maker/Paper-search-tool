"""Isolated TPAMI acceptance using normal public rendering; no production writes."""
import argparse
from pathlib import Path
import re
from run_venue import run_year,validate_request
from venue_runtime import FetchCache
from tpami_rendered import PublicRenderer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--years',nargs='+',type=int,required=True)
    parser.add_argument('--attempt',required=True)
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args();validate_request('TPAMI',args.years)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',args.attempt):parser.error('Invalid attempt name')
    root=Path(__file__).resolve().parent/'output/production_acceptance/attempts'/args.attempt
    results=[]
    for year in args.years:
        renderer=PublicRenderer(root/'TPAMI'/str(year),year)
        def factory(base,venue,year,**kwargs):
            def offline(url,**unused):raise ValueError('OFFLINE_CACHE_MISS: '+url)
            cache=FetchCache(base,venue,year,fetch=offline if args.offline else renderer,**kwargs)
            renderer.robots_loader=cache.get
            return cache
        try:
            result=run_year('TPAMI',year,root,cache_factory=factory)
            results.append(result);print(result,flush=True)
        finally:renderer.close()
    return 0 if all(r['status'] in ('SCREENED_CSV_READY','ALREADY_COMPLETE') for r in results) else 2


if __name__=='__main__':raise SystemExit(main())
