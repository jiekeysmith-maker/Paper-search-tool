"""Explicit screening of reliable PROVISIONAL rows; never changes the formal gate."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import uuid
import pandas as pd
from screen_verified import RULES, RULES_SHA256, MAPPING, venue_year_path
from src.screener import RuleEngine, build_audit_sample
from src.utils import load_yaml, SCREENING_COLUMNS, DECISION_KEEP, DECISION_MAYBE, DECISION_AMBIGUOUS, DECISION_DROP
from venue_runtime import job_lock, atomic_bytes, utc


def screen(venue,year,*,library_root,write=False,lock_held=False):
    base=venue_year_path(library_root,venue,year)
    if write and not lock_held:
        with job_lock(base,venue,year):
            return screen(venue,year,library_root=library_root,write=True,lock_held=True)
    rule_hash=sha256(RULES.read_bytes()).hexdigest()
    if rule_hash!=RULES_SHA256:
        raise ValueError('Frozen V1.2 hash mismatch')
    corpus=base/'raw/PROVISIONAL_Formal_Proceedings_Corpus.csv'
    audit_path=base/'raw/Audit_Summary.json'
    out=base/'screening_provisional'
    if any(p.resolve()!=p for p in (corpus,audit_path,out)):
        raise ValueError('Linked provisional paths are forbidden')
    if (base/'raw/Formal_Proceedings_Corpus.csv').exists():
        raise ValueError('Formal corpus exists; use screen_verified')
    audit_bytes=audit_path.read_bytes();audit=json.loads(audit_bytes)
    if (audit.get('status')!='REVIEW_REQUIRED' or audit.get('venue')!=venue or audit.get('year')!=year
            or type(audit.get('unresolved_count')) is not int or audit['unresolved_count']<0):
        raise ValueError('Provisional screening requires a scoped REVIEW_REQUIRED audit')
    data=corpus.read_bytes()
    frame=pd.read_csv(BytesIO(data),dtype=str,keep_default_na=False)
    required=set(MAPPING)|{'Formal_Publication_Evidence'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('No usable provisional corpus/schema')
    if not (frame.Venue.eq(venue)&frame.Year.eq(str(year))).all():
        raise ValueError('Provisional Venue-Year mismatch')
    for key in ('Paper_ID','Official_URL'):
        if frame[key].str.strip().eq('').any() or frame[key].duplicated().any():
            raise ValueError('Ambiguous provisional identity: '+key)
    usable=frame[list(required-{'PDF_URL'})].apply(lambda col:col.str.strip().ne('')).all(axis=1)
    usable &= ~frame.Authors.str.contains(r'\bet al\.?\b|…|\.\.\.',case=False,regex=True)
    excluded=frame[~usable].copy();records=frame[usable].to_dict('records')
    if not records:
        raise ValueError('No complete Title+Abstract metadata to screen')
    config=load_yaml(RULES);engine=RuleEngine(config)
    results=pd.DataFrame([engine.evaluate({label:r[key] for key,label in MAPPING.items()}) for r in records],columns=SCREENING_COLUMNS)
    flags=dict(Provisional_Screening=True,Corpus_Audit_Status=audit['status'],Corpus_Unresolved_Count=audit['unresolved_count'])
    tables={'KD_Screening.csv':results.assign(**flags)}
    counts={};candidates=[];by_id={r['Paper_ID']:r for r in records}
    for decision in (DECISION_KEEP,DECISION_MAYBE,DECISION_AMBIGUOUS,DECISION_DROP):
        part=results[results['Decision（筛选决定）']==decision];key=decision.split('（')[0]
        counts[key]=len(part);tables[key+'.csv']=part.assign(**flags)
        if decision!=DECISION_DROP:
            for r in part.to_dict('records'):
                candidates.append({**by_id[r['Paper_ID（论文编号）']],**flags,'Rule_Decision':key,
                    'Rule_Evidence':r['Decision_Reason（筛选理由）'],'Rules_Version':'V1.2','Rules_SHA256':rule_hash,
                    'Review_Status':'PENDING_HUMAN_SECONDARY_REVIEW','Data_Issue':'Corpus completeness remains unresolved'})
    candidate_columns=list(frame.columns)+[k for k in [*flags,'Rule_Decision','Rule_Evidence','Rules_Version','Rules_SHA256','Review_Status','Data_Issue'] if k not in frame.columns]
    tables['Needs_Secondary_Review.csv']=pd.DataFrame(candidates,columns=candidate_columns)
    drops=results[results['Decision（筛选决定）']==DECISION_DROP].copy()
    drops['Rule_Score（规则分数）']=pd.to_numeric(drops['Rule_Score（规则分数）'])
    tables['SAFE_DROP_Audit_Sample.csv']=build_audit_sample(drops,config).assign(**flags)
    tables['Metadata_Excluded_From_Screening.csv']=excluded.assign(**flags,Reason='Missing required metadata or truncated authors; identity retained')
    manifest=dict(utc=utc(),venue=venue,year=year,status='PROVISIONAL_SCREENED_CSV_READY' if write else 'VALIDATED_NO_WRITE',
        provisional_screening=True,corpus_audit_status=audit['status'],unresolved_count=audit['unresolved_count'],
        corpus_sha256=sha256(data).hexdigest(),audit_sha256=sha256(audit_bytes).hexdigest(),rules_sha256=rule_hash,
        input_count=len(frame),screened_count=len(records),metadata_excluded_count=len(excluded),counts=counts,candidate_count=len(candidates))
    if write:
        if out.exists():raise FileExistsError('Provisional screening already exists; preserved')
        staging=base/'runtime'/('provisional-'+uuid.uuid4().hex+'.partial');staging.mkdir(parents=True)
        for name,table in tables.items():table.to_csv(staging/name,index=False,encoding='utf-8-sig',lineterminator='\n')
        manifest['output_sha256']={name:sha256((staging/name).read_bytes()).hexdigest() for name in tables}
        atomic_bytes(staging/'Run_Manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
        staging.rename(out)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('venue');parser.add_argument('year',type=int)
    parser.add_argument('--library-root',type=Path,required=True);parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    print(json.dumps(screen(args.venue,args.year,library_root=args.library_root,write=args.write),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
