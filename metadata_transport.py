"""Bounded metadata retries; long Retry-After stops for a human decision."""
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import requests

class RetryDeferred(RuntimeError): pass
def retry_delay(value, fallback):
    if not value:return fallback
    try:return max(0,float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, (date-datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return fallback
def get_metadata(url,*,get=requests.get,sleep=time.sleep,attempts=3,history=None):
    if type(attempts) is not int or attempts < 1:
        raise ValueError('attempts must be a positive integer')
    history=[] if history is None else history
    for attempt in range(attempts):
        try:
            r=get(url,timeout=25,headers={'User-Agent':'PaperSearchTool/1.0 (public academic metadata research)'})
        except requests.RequestException as exc:
            history.append({'attempt':attempt+1,'error':type(exc).__name__})
            if attempt+1==attempts:raise
            sleep(2**attempt);continue
        history.append({'attempt':attempt+1,'http_status':r.status_code})
        if r.status_code not in (429,500,502,503,504):return r
        delay=retry_delay(r.headers.get('Retry-After'),2**attempt)
        history[-1]['retry_after_seconds']=delay
        if delay>60:raise RetryDeferred(f'Retry-After {delay}s; stopped. A human must choose when to retry.')
        if attempt+1==attempts:return r
        sleep(delay)
    raise RuntimeError('No response')
