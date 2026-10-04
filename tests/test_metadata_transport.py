import requests,pytest
from metadata_transport import get_metadata,RetryDeferred
from unittest.mock import Mock

def response(code,retry=None):
    r=requests.Response();r.status_code=code;r._content=b'ok'
    if retry:r.headers['Retry-After']=retry
    return r
def test_429_retry_after_then_success():
    get=Mock(side_effect=[response(429,'3'),response(200)]);sleep=Mock();history=[]
    assert get_metadata('https://example.test',get=get,sleep=sleep,history=history).status_code==200
    sleep.assert_called_once_with(3);assert len(history)==2
def test_disconnect_retry_success():
    get=Mock(side_effect=[requests.ConnectionError('offline'),response(200)]);history=[]
    assert get_metadata('https://example.test',get=get,sleep=Mock(),history=history).status_code==200
    assert history[0]['error']=='ConnectionError'
def test_exhausted_disconnect_is_visible():
    history=[]
    with pytest.raises(requests.ConnectionError):get_metadata('https://example.test',get=Mock(side_effect=requests.ConnectionError()),sleep=Mock(),history=history)
    assert len(history)==3
def test_long_retry_after_defers_without_early_request():
    get=Mock(return_value=response(429,'120'));sleep=Mock()
    with pytest.raises(RetryDeferred):get_metadata('https://example.test',get=get,sleep=sleep)
    assert get.call_count==1;sleep.assert_not_called()
def test_403_no_retry():
    get=Mock(return_value=response(403));assert get_metadata('https://example.test',get=get).status_code==403
    assert get.call_count==1

@pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
def test_exhausted_http_error_remains_visible(status):
    get = Mock(return_value=response(status)); sleep = Mock(); history = []
    result = get_metadata('https://example.test', get=get, sleep=sleep, history=history)
    with pytest.raises(requests.HTTPError):
        result.raise_for_status()
    assert get.call_count == 3 and sleep.call_count == 2 and len(history) == 3

def test_invalid_retry_after_uses_backoff():
    get = Mock(side_effect=[response(503, 'invalid'), response(200)])
    sleep = Mock()
    get_metadata('https://example.test', get=get, sleep=sleep)
    sleep.assert_called_once_with(1)

def test_http_date_retry_after():
    from metadata_transport import retry_delay
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
    assert 28 <= retry_delay(future, 1) <= 30

def test_zero_attempts_rejected():
    with pytest.raises(ValueError):
        get_metadata('https://example.test', attempts=0)


def test_last_attempt_long_retry_after_is_explicit():
    with pytest.raises(RetryDeferred, match='120'):
        get_metadata('https://example.test', get=Mock(return_value=response(429, '120')), attempts=1)
