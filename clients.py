"""HTTP clients with sanitized errors: never print tokens, URLs or service keys."""
import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

class TransportError(RuntimeError):pass

class ApiError(RuntimeError):
    def __init__(self,status,message):
        super().__init__(message);self.status=status

class TelegramError(RuntimeError):
    def __init__(self,code,retry_after=None):
        super().__init__(f'Telegram operation failed ({code})')
        self.code=code;self.retry_after=retry_after


def validate_backend_url(url):
    parts=urlsplit(url)
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in url):
        raise ValueError('BACKEND_URL must be a URL without credentials, query or fragment')
    parts.port
    if parts.scheme=='https':return url.rstrip('/')
    private=parts.hostname in {'localhost','127.0.0.1','::1'} or '.' not in parts.hostname
    try:private=private or ipaddress.ip_address(parts.hostname).is_private
    except ValueError:pass
    if parts.scheme!='http' or not private:raise ValueError('Public BACKEND_URL must use HTTPS')
    return url.rstrip('/')


class BackendClient:
    def __init__(self,url,key):
        self.url=validate_backend_url(url)
        if len(key)<32:raise ValueError('TELEGRAM_SERVICE_KEY must contain at least 32 characters')
        self.key=key
    def request(self,method,path,body=None,*,tid=None,request_id=None,generation=None):
        headers={'Content-Type':'application/json','X-Telegram-Service-Key':self.key}
        if tid is not None:headers['X-Telegram-User-ID']=str(tid)
        if request_id is not None:headers['X-Telegram-Request-ID']=str(request_id)
        if generation:headers['X-Telegram-Binding']=generation
        request=Request(self.url+'/api/telegram/'+path,method=method,headers=headers,
                        data=json.dumps(body,ensure_ascii=False,sort_keys=True).encode() if body is not None else None)
        try:
            with urlopen(request,timeout=15) as response:return json.load(response)
        except HTTPError as error:
            try:data=json.load(error)
            except (ValueError,OSError):data={}
            detail=data.get('detail') if isinstance(data,dict) else None
            if not isinstance(detail,str):detail='Проверьте введённые данные' if error.code==422 else 'Операция недоступна'
            raise ApiError(error.code,detail[:500]) from None
        except (URLError,OSError,ValueError):raise TransportError('Сервис Наследия временно недоступен') from None


class TelegramClient:
    def __init__(self,token):self.token=token
    def call(self,method,payload):
        request=Request(f'https://api.telegram.org/bot{self.token}/{method}',data=json.dumps(payload,ensure_ascii=False).encode(),
                        headers={'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=40 if method=='getUpdates' else 20) as response:data=json.load(response)
        except HTTPError as error:
            try:data=json.load(error)
            except (ValueError,OSError):data={'ok':False,'error_code':error.code}
        except (URLError,OSError,ValueError):raise TransportError(f'Telegram {method} temporarily unavailable') from None
        if not isinstance(data,dict) or data.get('ok') is not True:
            code=data.get('error_code',500) if isinstance(data,dict) else 500
            retry=data.get('parameters',{}).get('retry_after') if isinstance(data,dict) else None
            raise TelegramError(code,min(86400,max(1,int(retry))) if isinstance(retry,(int,float)) else None)
        return data.get('result')
