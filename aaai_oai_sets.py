"""Enumerate official annual OAI sets and each complete ListRecords chain."""
import json
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from aaai_pipeline import ROOT, NS, enumerate_oai, OAIIncomplete
from venue_runtime import write_json, write_csv


def list_sets(cache):
    url=ROOT+'?verb=ListSets'
    seen,rows,pages=set(),[],[]
    expected=None
    while url:
        if url in seen or len(seen)>=1000:
            raise ValueError('OAI ListSets token loop/limit')
        seen.add(url)
        tree=ET.fromstring(cache.get(url))
        error=tree.find('o:error',NS)
        if error is not None:
            raise ValueError(f'OAI ListSets error: {error.text}')
        items=tree.findall('.//o:set',NS)
        if not items:
            raise ValueError('OAI ListSets empty page')
        token=tree.find('.//o:resumptionToken',NS)
        if token is not None:
            if token.get('cursor') and int(token.get('cursor'))!=len(rows):
                raise ValueError('OAI ListSets cursor gap')
            if token.get('completeListSize'):
                total=int(token.get('completeListSize'))
                if expected is not None and expected!=total:
                    raise ValueError('OAI ListSets total changed')
                expected=total
        for item in items:
            spec=item.findtext('o:setSpec',namespaces=NS)
            name=item.findtext('o:setName',namespaces=NS)
            if not spec or not name:
                raise ValueError('OAI ListSets missing identity/name')
            rows.append(dict(set_spec=spec,name=name.strip(),evidence_url=url))
        following=(token.text or '').strip() if token is not None else ''
        pages.append(dict(url=url,count=len(items),cumulative=len(rows),next_token=following))
        write_json(cache.base/'raw/OAI_Set_Inventory.json',dict(status='ENUMERATING',sets=rows,pages=pages))
        if not following:
            if expected is not None and len(rows)!=expected:
                raise ValueError('OAI ListSets truncated final token')
            break
        if expected is not None and len(rows)>=expected:
            raise ValueError('OAI ListSets continuation beyond total')
        url=ROOT+'?'+urlencode(dict(verb='ListSets',resumptionToken=following))
    write_json(cache.base/'raw/OAI_Set_Inventory.json',dict(status='COMPLETE',sets=rows,pages=pages))
    return rows


def annual_records(cache,year):
    rows,chains=[],[]
    try:
        inventory=list_sets(cache)
        # Published OJS set identifiers encode the annual series. Each returned
        # article must independently confirm the matching volume in dc:source.
        pattern=rf'AAAI:(?:AI{str(year)[-2:]}-\d+|{year}-\d+)'
        selected=[r for r in inventory if re.fullmatch(pattern,r['set_spec'])]
        if not selected:
            raise ValueError('No official annual OAI sets')
        if len({r['set_spec'] for r in selected})!=len(selected):
            raise ValueError('Duplicate target-year OAI set identity')
        write_json(cache.base/'raw/OAI_Annual_Set_Selection.json',dict(year=year,selection_pattern=pattern,sets=selected))
        for item in selected:
            try:
                part=enumerate_oai(cache,year,None,set_spec=item['set_spec'])
            except OAIIncomplete as exc:
                rows.extend(exc.rows)
                raise
            rows.extend(part)
            chains.append(dict(**item,status='COMPLETE',records=len(part)))
            write_json(cache.base/'raw/OAI_Annual_Chains.json',dict(status='ENUMERATING',chains=chains))
            write_csv(cache.base/'raw/OAI_Annual_Records.csv',rows)
        write_json(cache.base/'raw/OAI_Annual_Chains.json',dict(status='COMPLETE',chains=chains,sets=len(selected),records=len(rows)))
        return rows
    except Exception as exc:
        write_json(cache.base/'raw/OAI_Annual_Chains.json',dict(status='INCOMPLETE',chains=chains,error=repr(exc)))
        write_csv(cache.base/'raw/OAI_Annual_Records.csv',rows)
        raise OAIIncomplete(exc,rows) from exc
