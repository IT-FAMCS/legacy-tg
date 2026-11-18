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
                if row['method']=='menu':
                    self.deliver_menu(row)
                else:
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

    def deliver_menu(self,row):
        payload=dict(row['payload'])
        new_menu=payload.pop('_new_menu',False)
        chat_id=payload['chat_id']
        target_id=payload.pop('_menu_message_id',None)
        message_id=self.store.menu_session(chat_id,new_menu) if new_menu else target_id or self.store.menu_message(chat_id)
        if message_id:
            try:
                self.telegram.call('editMessageText',{**payload,'message_id':message_id})
            except TelegramError as error:
                if error.reason=='unchanged':
                    self.store.menu_sent(row['id'],chat_id,message_id,new_menu)
                    return
                if error.reason!='missing':
                    raise
            else:
                self.store.menu_sent(row['id'],chat_id,message_id,new_menu)
                return
        result=self.telegram.call('sendMessage',payload)
        if not isinstance(result,dict) or type(result.get('message_id')) is not int or result['message_id']<=0:
            raise TransportError('Telegram did not return a menu message ID')
        self.store.menu_sent(row['id'],chat_id,result['message_id'],new_menu)

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
                    validation=self.backend.request('POST',f"service/notifications/{row['id']}/validate",
                        {'lease_token':row['lease_token']})
                    if not validation['valid']:
                        self.backend.request('POST',f"service/notifications/{row['id']}/ack",
                            {'lease_token':row['lease_token'],'outcome':'permanent'})
                        continue
                    payload['text']=validation['text']
                    if row['kind']!='removed':
                        token=self.store.callback(row['telegram_user_id'],row['generation'],{'kind':'task','id':row['task_id'],'new_menu':True},persistent=True)
                        payload['reply_markup']={'inline_keyboard':[[{'text':'Открыть таску','style':'primary','callback_data':token}]]}
                    self.telegram.call('sendMessage',payload)
            except ApiError as error:
                outcome='permanent' if error.status in {403,404,410} else 'retry'
            except TelegramError as error:
                if error.code==429:self.store.pause_sending(error.retry_after or 30)
                outcome='blocked' if error.code==403 else 'permanent' if error.code in {400,401,404} else 'retry'
                if error.retry_after:extra['retry_after']=error.retry_after
            except TransportError:outcome='retry'
            self.backend.request('POST',f"service/notifications/{row['id']}/ack",{'lease_token':row['lease_token'],'outcome':outcome,**extra})
