import pytest
import venue_runtime as runtime


@pytest.mark.parametrize('permanent',[False,True])
def test_replace_sharing_violation_is_bounded_and_preserves_old_file(tmp_path,monkeypatch,permanent):
    path=tmp_path/'Progress.json';path.write_bytes(b'old')
    replace=runtime.os.replace;calls=[]
    def locked(src,dst):
        calls.append(1)
        if permanent or len(calls)<3:
            raise PermissionError('Windows sharing violation')
        return replace(src,dst)
    monkeypatch.setattr(runtime.os,'replace',locked)
    monkeypatch.setattr(runtime.time,'sleep',lambda _:None)
    if permanent:
        with pytest.raises(PermissionError):runtime.atomic_bytes(path,b'new')
        assert path.read_bytes()==b'old' and len(calls)==6
    else:
        runtime.atomic_bytes(path,b'new')
        assert path.read_bytes()==b'new' and len(calls)==3
    assert not list(tmp_path.glob('*.tmp'))
