"""AAAI annual OJS/OAI enumeration. Public metadata only, no PDF retrieval."""
from collections import Counter
from hashlib import sha256
import json
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from venue_runtime import write_json, write_csv, utc

NS = {'o': 'http://www.openarchives.org/OAI/2.0/', 'dc': 'http://purl.org/dc/elements/1.1/'}
ROOT = 'https://ojs.aaai.org/index.php/AAAI/oai'


class OAIIncomplete(ValueError):
    """Enumeration failed, but successfully parsed official records remain usable."""
    def __init__(self, cause, rows):
        super().__init__(str(cause))
        self.rows, self.cause = rows, cause


def enumerate_oai(cache, year, target_issues, *, set_spec=None):
    url = ROOT + '?verb=ListRecords&metadataPrefix=oai_dc'
    raw = cache.base / 'raw'
    if set_spec:
        url += '&' + urlencode({'set':set_spec})
        raw = raw / 'OAI_Sets' / sha256(set_spec.encode()).hexdigest()[:16]
    seen, rows, pages, identifiers, duplicates, deletions = set(), [], [], {}, [], []
    expected, consumed = None, 0
    try:
        while url:
            if url in seen or len(seen) >= 10000:
                raise ValueError('OAI pagination loop/limit')
            seen.add(url)
            tree = ET.fromstring(cache.get(url))
            error = tree.find('o:error', NS)
            if error is not None:
                if set_spec and len(seen)==1 and error.get('code')=='noRecordsMatch':
                    write_json(raw/'OAI_Pagination.json',dict(status='COMPLETE',set_spec=set_spec,pages=[],consumed=0,expected=0,no_records_evidence=url))
                    return []
                raise ValueError(f'OAI error {error.get("code")}: {error.text}')
            records = tree.findall('.//o:record', NS)
            if not records:
                raise ValueError('Empty OAI page')
            token = tree.find('.//o:resumptionToken', NS)
            if token is not None and token.get('completeListSize'):
                total = int(token.get('completeListSize'))
                if expected is not None and total != expected:
                    raise ValueError('OAI completeListSize changed during enumeration')
                expected = total
            if token is not None and token.get('cursor') and int(token.get('cursor')) != consumed:
                raise ValueError('OAI cursor gap/repeated page')
            for record in records:
                header = record.find('o:header', NS)
                identity = header.findtext('o:identifier', default='', namespaces=NS) if header is not None else ''
                if identity:
                    digest = sha256(ET.tostring(record)).hexdigest()
                    if identity in identifiers:
                        old_hash, old_url = identifiers[identity]
                        exact = old_hash == digest
                        duplicates.append(dict(OAI_Identifier=identity, First_URL=old_url, Repeated_URL=url,
                                               First_SHA256=old_hash, Repeated_SHA256=digest,
                                               Status='EXACT_DUPLICATE_EXPORT' if exact else 'CONFLICTING_DUPLICATE_IDENTITY'))
                        if exact:
                            continue
                    else:
                        identifiers[identity] = (digest, url)
                if header is not None and header.get('status') == 'deleted':
                    deletions.append(dict(OAI_Identifier=identity,Datestamp=header.findtext('o:datestamp',default='',namespaces=NS),Evidence_URL=url))
                    continue
                if set_spec and (header is None or set_spec not in [x.text for x in header.findall('o:setSpec',NS)]):
                    raise ValueError('OAI record lacks requested set membership')
                def values(name):
                    return [''.join(x.itertext()).strip() for x in record.findall('.//dc:' + name, NS)]
                sources = values('source')
                english = next((s for s in sources if 'Proceedings of the AAAI Conference on Artificial Intelligence' in s), '')
                scope = re.search(r'Vol\.?\s*(\d+)\D+No\.?\s*(\d+)', english, re.I)
                if not scope:
                    raise ValueError('OAI source cannot establish volume/issue scope')
                if set_spec and int(scope[1]) != year - 1986:
                    raise ValueError('Annual OAI set contains wrong publication volume')
                if int(scope[1]) != year - 1986 or (target_issues is not None and int(scope[2]) not in target_issues):
                    continue
                ids = values('identifier')
                article = next((u for u in ids if re.fullmatch(r'https://ojs.aaai.org/index.php/AAAI/article/view/\d+', u)), '')
                if not article:
                    raise ValueError('OAI missing stable article URL')
                doi = next((u for u in ids if u.startswith('10.1609/aaai.')), '')
                relations = values('relation')
                pdf = next((u for u in relations if re.fullmatch(re.escape(article) + r'/\d+', u)), '') if 'application/pdf' in values('format') else ''
                authors = [' '.join(reversed(n.split(', ', 1))) if ', ' in n else n for n in values('creator')]
                descriptions = values('description')
                rows.append(dict(Title=(values('title') or [''])[0], Official_URL=article,
                                 Authors='; '.join(authors), Volume=int(scope[1]), Issue=int(scope[2]),
                                 DOI=doi, PDF_URL=pdf, Abstract=BeautifulSoup(descriptions[0], 'html.parser').get_text(' ', strip=True) if descriptions else '',
                                 Published_Date=(values('date') or [''])[0], Evidence_URL=url,
                                 OAI_Identifier=identity, OAI_Source=english,
                                 OAI_Sets='; '.join(x.text or '' for x in header.findall('o:setSpec', NS)) if header is not None else ''))
            consumed += len(records)
            following = (token.text or '').strip() if token is not None else ''
            pages.append(dict(url=url, records=len(records), cumulative=consumed, next_token=following,
                              expected=expected, expiration=token.get('expirationDate') if token is not None else None))
            write_json(raw / 'OAI_Pagination.json', dict(status='ENUMERATING', set_spec=set_spec, pages=pages, consumed=consumed, expected=expected))
            write_csv(raw / 'OAI_Annual_Records.csv', rows)
            write_csv(raw / 'OAI_Duplicate_Evidence.csv', duplicates)
            write_csv(raw / 'OAI_Deletion_Evidence.csv', deletions)
            if not following:
                if expected is not None and consumed != expected:
                    raise ValueError(f'OAI missing/truncated final token: {consumed}/{expected}')
                break
            if expected is not None and consumed >= expected:
                raise ValueError('OAI token continues beyond declared completeListSize')
            url = ROOT + '?' + urlencode({'verb': 'ListRecords', 'resumptionToken': following})
        if set_spec and any(r['Status']=='CONFLICTING_DUPLICATE_IDENTITY' for r in duplicates):
            raise ValueError('Conflicting active/deleted OAI identities in annual set; evidence review required')
        write_json(raw / 'OAI_Pagination.json', dict(status='COMPLETE', set_spec=set_spec, pages=pages, consumed=consumed, expected=expected))
        return rows
    except Exception as exc:
        write_json(raw / 'OAI_Pagination.json', dict(status='INCOMPLETE', set_spec=set_spec, pages=pages, consumed=consumed, expected=expected, error=repr(exc)))
        write_csv(raw / 'OAI_Annual_Records.csv', rows)
        write_csv(raw / 'OAI_Duplicate_Evidence.csv', duplicates)
        write_csv(raw / 'OAI_Deletion_Evidence.csv', deletions)
        raise OAIIncomplete(exc, rows) from exc


def from_oai(index, record, year):
    """OAI is an official article metadata export; issue TOC supplies section scope."""
    native = index['Native_Publisher_ID']
    expected = f'10.1609/aaai.v{index["Volume"]}i{index["Issue"]}.{native}'
    if (record['DOI'] != expected or record['Official_URL'] != index['Official_URL']
            or int(record['Volume']) != year - 1986 or str(record['Issue']) != index['Issue']
            or not record['Published_Date'].startswith(str(year))):
        raise ValueError('OAI article DOI/issue/year contract mismatch')
    if not record['Title'] or not record['Abstract'] or not record['Authors']:
        raise ValueError('OAI article metadata incomplete')
    return dict(Paper_ID=f'AAAI{year}_OJS_{native}', Title=record['Title'], Authors=record['Authors'],
                Abstract=record['Abstract'], Venue='AAAI', Year=year, Track=index['Track'],
                Official_URL=index['Official_URL'], PDF_URL=record.get('PDF_URL', ''), DOI=record['DOI'],
                Volume=index['Volume'], Issue=index['Issue'], Published_Date=record['Published_Date'],
                Formal_Publication_Evidence=f'AAAI Press OJS issue {index["Issue_URL"]}; section {index["Track"]}; official OAI published article {record["DOI"]}',
                Metadata_Source=record['Evidence_URL'], Abstract_Source_URL=record['Evidence_URL'],
                Retrieval_Source=record['Evidence_URL'],
                BibTeX_or_Publisher_Metadata=json.dumps(record, ensure_ascii=False))


def quality_report(collection):
    """Metadata usability is reported separately; this never authorizes screening."""
    base=collection.cache.base
    by_url={r['Official_URL']:r for r in collection.corpus}
    missing={k:0 for k in ('Title','Authors','Abstract','PDF_URL','DOI')}
    for paper in collection.publisher:
        row=by_url.get(paper['Official_URL'],paper)
        for key in missing:
            missing[key]+=not bool(str(row.get(key,'')).strip())
    duplicates={key:sum(n-1 for value,n in Counter(str(r.get(key,'')) for r in collection.corpus).items() if value and n>1)
                for key in ('Paper_ID','Title','Official_URL','DOI')}
    usable=sum(all(str(r.get(k,'')).strip() for k in ('Paper_ID','Title','Authors','Abstract','Official_URL','DOI')) for r in collection.corpus)
    write_json(base/'reports/Corpus_Quality.json',dict(
        venue='AAAI',year=collection.cache.year,expected=len(collection.publisher),retrieved=len(collection.corpus),
        unique=len(by_url),duplicates=duplicates,missing=missing,metadata_complete_rows=usable,
        corpus_identity='PROVISIONAL',independent_enumeration_complete=collection.evidence_complete,
        formal_screening_authorized=False,
        next_action='Read raw/Audit_Summary.json and raw/Open_Issues.csv; incomplete evidence never grants VERIFIED'))
