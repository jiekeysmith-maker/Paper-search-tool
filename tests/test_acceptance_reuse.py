import json
from hashlib import sha256
import pytest
from accept_production import AcceptanceCache


def test_acceptance_reads_without_copying_and_rechecks_hash(tmp_path):
    source=tmp_path/'old.body';source.write_bytes(b'official fixture')
    url='https://link.springer.com/book/10.1007/example'
    entry=dict(url=url,snapshot=str(source),sha256=sha256(source.read_bytes()).hexdigest(),bytes=source.stat().st_size,http_status=200)
    registry=tmp_path/'registry.json';registry.write_text(json.dumps(dict(venue='ECCV',year=2024,source_snapshots=[entry])))
    def no_network(*a,**k):pytest.fail('Unexpected network')
    cache=AcceptanceCache(tmp_path/'attempt','ECCV',2024,fetch=no_network,delay=0)
    cache.import_registry(registry,{url})
    assert cache.get(url)=='official fixture'
    assert cache.has_snapshot(url)
    assert not cache.paths(url)[0].exists()
    assert cache.manifest()[0]['read_only_reuse']
    source.write_bytes(b'changed evidence')
    with pytest.raises(ValueError,match='integrity'):cache.get(url)


def test_acceptance_scope_is_not_inferred_from_path(tmp_path):
    registry=tmp_path/'registry.json';registry.write_text(json.dumps(dict(venue='ECCV',year=2026,source_snapshots=[])))
    cache=AcceptanceCache(tmp_path/'attempt','ECCV',2024,delay=0)
    with pytest.raises(ValueError,match='scope'):cache.import_registry(registry,set())


def test_missing_reused_snapshot_cannot_trigger_cache_only_network(tmp_path):
    url='https://link.springer.com/book/10.1007/example'
    def forbidden(*a,**k):pytest.fail('Cache-only request touched network')
    cache=AcceptanceCache(tmp_path/'attempt','ECCV',2024,fetch=forbidden)
    cache.reusable[url]=dict(snapshot=str(tmp_path/'gone.body'))
    assert not cache.has_snapshot(url)
    with pytest.raises(ValueError,match='CACHE_ONLY_MISS'):cache.get(url,cache_only=True)
