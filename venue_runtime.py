"""Small, foreground-only Venue-Year storage primitives."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import csv
import io
import json
import os
import socket
import time
import uuid
from urllib.parse import urlparse

from metadata_transport import get_metadata


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep temporary names short enough for ordinary Windows path limits.
    temp = path.with_name('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for row in rows for k in row)) or ['Status', 'Reason']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(path, stream.getvalue().encode('utf-8-sig'))


def check_paths(base):
    for path in [base, *(base / x for x in ('raw', 'runtime', 'screening', 'logs', 'reports'))]:
        if path.resolve() != path:
            raise ValueError(f'Linked destination is forbidden: {path}')


@contextmanager
def job_lock(base, venue, year):
    check_paths(base)
    path = base / 'runtime' / 'run.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    value = dict(pid=os.getpid(), start_time=utc(), hostname=socket.gethostname(),
                 venue=venue, year=year, token=token)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(f'JOB_LOCKED: {path}; active or stale lock. '
                           'Never removed automatically; inspect PID/host/start_time manually.') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        if path.exists() and json.loads(path.read_text(encoding='utf-8')).get('token') == token:
            path.unlink()


HOSTS = {
    'ICML': {'proceedings.mlr.press', 'icml.cc'},
    'ICLR': {'proceedings.iclr.cc', 'iclr.cc'},
    'AAAI': {'aaai.org', 'www.aaai.org', 'ojs.aaai.org'},
    'ECCV': {'link.springer.com', 'www.ecva.net', 'ecva.net', 'eccv.ecva.net'},
    'TPAMI': {'ieeexplore.ieee.org', 'www.computer.org'},
}


class FetchCache:
    """Cache success bodies and hashes, never an HTTP failure. Caller owns job lock."""
    def __init__(self, base, venue, year, *, fetch=get_metadata, delay=2.0, refresh_evidence=False):
        self.base, self.venue, self.year = Path(base).absolute(), venue, year
        self.directory = self.base / 'runtime' / 'cache'
        if self.directory.resolve() != self.directory:
            raise ValueError('Linked cache directories are forbidden')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fetch, self.delay, self.last = fetch, delay, 0.0
        self.hits, self.requests = 0, 0
        self.refresh_evidence, self.refreshed = refresh_evidence, set()

    def validate_url(self, url):
        p = urlparse(url)
        if p.scheme != 'https' or p.hostname not in HOSTS[self.venue] or p.username or p.port not in (443, None):
            raise ValueError(f'Non-official metadata URL: {url}')
        if p.path.lower().endswith('.pdf') or '/article/download/' in p.path:
            raise ValueError('PDF/download requests are forbidden')
        if self.venue == 'TPAMI' and (p.path.startswith(('/rest', '/ielx')) or p.path.startswith('/api/')):
            raise ValueError('Restricted IEEE endpoint forbidden')

    def paths(self, url):
        key = sha256(url.encode()).hexdigest()
        return self.directory / (key + '.body'), self.directory / (key + '.json')

    def get(self, url):
        self.validate_url(url)
        body, meta = self.paths(url)
        is_detail = ('/article/view/' in url or '/chapter/' in url or '/hash/' in url
                     or (self.venue == 'ICML' and urlparse(url).path.endswith('.html')))
        refresh = self.refresh_evidence and not is_detail and url not in self.refreshed
        if body.exists() and meta.exists():
            m = json.loads(meta.read_text(encoding='utf-8'))
            data = body.read_bytes()
            if (m.get('url') != url or m.get('venue') != self.venue or m.get('year') != self.year
                    or m.get('http_status') != 200 or m.get('sha256') != sha256(data).hexdigest()):
                raise ValueError(f'CACHE_INTEGRITY_FAILURE: {meta}')
            if not refresh:
                self.hits += 1
                return data.decode('utf-8-sig')
            # Explicit manual source refresh preserves previous successful evidence.
            history_dir = self.directory / 'history'
            history_id = uuid.uuid4().hex
            atomic_bytes(history_dir / (history_id + '.body'), data)
            atomic_bytes(history_dir / (history_id + '.json'), meta.read_bytes())
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        history = []
        self.requests += 1
        print(f'{self.venue} {self.year}: FETCH {self.requests} {url}', flush=True)
        try:
            response = self.fetch(url, history=history)
            response.raise_for_status()
            self.validate_url(response.url or url)
            if response.status_code != 200:
                raise ValueError(f'Expected HTTP 200, got {response.status_code}')
            if 'application/pdf' in response.headers.get('Content-Type', '').lower():
                raise ValueError('Unexpected PDF response')
            data = response.content
            data.decode('utf-8-sig')  # fail before publishing unusable cache
            atomic_bytes(body, data)
            write_json(meta, dict(url=url, final_url=response.url, venue=self.venue,
                                 year=self.year, utc=utc(), http_status=200,
                                 sha256=sha256(data).hexdigest(), bytes=len(data), history=history))
            self.refreshed.add(url)
            return data.decode('utf-8-sig')
        except Exception as exc:
            write_json(self.base / 'logs' / (uuid.uuid4().hex + '-request-error.json'),
                       dict(url=url, utc=utc(), error=repr(exc), history=history))
            raise
        finally:
            self.last = time.monotonic()

    def import_registry(self, registry, allowed_urls):
        """Read-only legacy snapshot import. No code, state, or audit status is imported."""
        if self.refresh_evidence or not registry.exists():
            return
        data = json.loads(registry.read_text(encoding='utf-8-sig'))
        if data.get('venue') != self.venue or data.get('year') != self.year:
            raise ValueError('Legacy source registry scope mismatch')
        for entry in data.get('source_snapshots', []):
            url = entry.get('url')
            if url not in allowed_urls:
                continue
            self.validate_url(url)
            body, meta = self.paths(url)
            if body.exists() or meta.exists():
                continue
            source = Path(entry['snapshot'])
            if not source.is_file():
                continue  # unavailable legacy evidence: fetch from official source instead
            payload = source.read_bytes()
            if (entry.get('http_status') != 200 or entry.get('sha256') != sha256(payload).hexdigest()
                    or entry.get('bytes') != len(payload)):
                raise ValueError(f'Legacy snapshot integrity failure: {source}')
            payload.decode('utf-8-sig')
            atomic_bytes(body, payload)
            write_json(meta, dict(url=url, venue=self.venue, year=self.year, http_status=200,
                                 sha256=sha256(payload).hexdigest(), bytes=len(payload),
                                 utc=entry.get('utc'), imported_from=str(source)))

    def manifest(self):
        entries = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(self.directory.glob('*.json'))]
        write_json(self.base / 'runtime' / 'Fetch_Manifest.json', entries)
        return entries
