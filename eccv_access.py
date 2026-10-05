"""Per-run official-host stop signals; no network fallback during cache replay."""
from urllib.parse import urlparse
import re
from metadata_transport import RetryDeferred
from venue_runtime import write_json


class ECCVAccess:
    def __init__(self,cache):
        self.cache=cache
        self.blocked={}

    def get(self,url,*,cache_only=False):
        host=urlparse(url).hostname
        try:
            html=self.cache.get(url,cache_only=cache_only or host in self.blocked)
            title=re.search(r'<title[^>]*>(.*?)</title>',html,re.I|re.S)
            if title and re.search(r'access denied|authentication required|verify you are human|just a moment',title[1],re.I):
                raise ValueError('OFFICIAL_ACCESS_RESTRICTION: '+title[1].strip())
            return html
        except Exception as exc:
            response=getattr(exc,'response',None)
            if (isinstance(exc,RetryDeferred) or (response is not None and response.status_code in (401,403,429))
                    or 'idp.springer.com/auth' in str(exc) or 'OFFICIAL_ACCESS_RESTRICTION:' in str(exc)):
                self.blocked.setdefault(host,dict(url=url,error=repr(exc)))
                write_json(self.cache.base/'raw/Access_Restrictions.json',self.blocked)
            raise
