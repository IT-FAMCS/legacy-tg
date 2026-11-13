"""Delivery worker independent from long polling; no real sends in unit tests."""
import time
from clients import ApiError, TelegramError, TransportError

class DeliveryWorker:
    def __init__(self,backend,telegram,store):
        self.backend,self.telegram,self.store=backend,telegram,store
        self.last_backend_ok=0

    def flush_replies(self,limit=5):
        if not self.store.sending_allowed():return
        blocked=set()
        for row in self.store.outgoing(due_only=True)[:limit]:
            if row['tid'] in blocked:
                self.store.delivered(row['id']);continue
            try:
                if row['generation']:
                    me=self.backend.request('GET','bot/me',tid=row['tid'])
                    self.last_backend_ok=time.monotonic()
                    if me['generation']!=row['generation'] or me.get('blocked'):
                        self.store.delivered(row['id']);continue
                    for task_id in row['task_ids']:
                        self.backend.request('GET',f'bot/events/{task_id[6:]}/responsibility-zones' if isinstance(task_id,str) and task_id.startswith('event:') else f'bot/tasks/{task_id}',tid=row['tid'],generation=row['generation'])
                self.telegram.call(row['method'],row['payload'])
                self.store.delivered(row['id'])
            except ApiError as error:
                if error.status in {403,404,410}:self.store.delivered(row['id'])
                else:self.store.retry(row['id'],30)
            except TelegramError as error:
                if error.code==429:
                    self.store.pause_sending(error.retry_after or 30)
                    self.store.retry(row['id'],error.retry_after or 30)
                    return
                if error.code in {400,401,403,404}:
                    self.store.delivered(row['id'])
                    if error.code==403:
                        blocked.add(row['tid'])
                        if row['generation']:
                            try:self.backend.request('POST','bot/blocked',{},tid=row['tid'],request_id='blocked:'+str(row['id']),generation=row['generation'])
                            except (ApiError,TransportError):pass
                else:self.store.retry(row['id'],error.retry_after or min(3600,5*2**min(row['attempts'],8)))
            except TransportError:self.store.retry(row['id'],min(3600,5*2**min(row['attempts'],8)))

    def notifications(self):
        if not self.store.sending_allowed():return
        # A single leased entry avoids exhausting its lease behind slow API calls.
        rows=self.backend.request('POST','service/notifications/claim',{'limit':1})
        self.last_backend_ok=time.monotonic()
        for row in rows:
            outcome='delivered';extra={}
            try:
                me=self.backend.request('GET','bot/me',tid=row['telegram_user_id'])
                if me['generation']!=row['generation'] or me.get('blocked'):
                    outcome='permanent'
                else:
                    payload={'chat_id':row['chat_id'],'text':row['text'],'link_preview_options':{'is_disabled':True}}
                    if row['kind']!='removed':
                        # Validate current assignment/access again immediately before the external send.
                        self.backend.request('GET',f"bot/tasks/{row['task_id']}",tid=row['telegram_user_id'],generation=row['generation'])
                        token=self.store.callback(row['telegram_user_id'],row['generation'],{'kind':'task','id':row['task_id']})
                        payload['reply_markup']={'inline_keyboard':[[{'text':'Открыть таску','callback_data':token}]]}
                    self.telegram.call('sendMessage',payload)
            except ApiError as error:
                outcome='permanent' if error.status in {403,404,410} else 'retry'
            except TelegramError as error:
                if error.code==429:self.store.pause_sending(error.retry_after or 30)
                outcome='blocked' if error.code==403 else 'permanent' if error.code in {400,401,404} else 'retry'
                if error.retry_after:extra['retry_after']=error.retry_after
            except TransportError:outcome='retry'
            self.backend.request('POST',f"service/notifications/{row['id']}/ack",{'lease_token':row['lease_token'],'outcome':outcome,**extra})
