"""Delivery worker independent from long polling; no real sends in unit tests."""
import time
from pathlib import Path
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


    def budget_notifications(self):
        if not self.store.sending_allowed():return
        rows=self.backend.request('POST','budget/notifications/claim',{'limit':1})
        self.last_backend_ok=time.monotonic()
        for event in rows:
            extra={};outcome='delivered';kind=event['event_type']
            endpoint=f"budget/notifications/{event['id']}"
            def validate():
                return self.backend.request('POST',endpoint+'/validate',{'lease_token':event['lease_token']})
            try:
                current=validate()
                if not current['valid']:
                    if event.get('detail_message_id'):
                        self.budget_edit('editMessageReplyMarkup',{'chat_id':event['chat_id'],'message_id':event['detail_message_id'],'reply_markup':{'inline_keyboard':[]}})
                    outcome='permanent'
                elif kind=='processed':
                    if event.get('detail_message_id'):
                        payload={'chat_id':event['chat_id'],'message_id':event['detail_message_id'],'reply_markup':{'inline_keyboard':[]}}
                        if current.get('cleanup_only'):
                            self.budget_edit('editMessageReplyMarkup',payload)
                        else:
                            row=current['request']
                            self.budget_edit('editMessageText',{**payload,'text':budget_text(row)+'\nЗаявка обработана: '+('одобрена' if row['status']=='approved' else 'отклонена')})
                elif kind=='outcome':
                    row=current['request']
                    result=f"Заявка №{row['id']} {'одобрена' if row['status']=='approved' else 'отклонена'}. Сумма: {row['amount']} BYN."
                    text=result+'\n'+row['description']
                    if row['status']=='approved':
                        fits=len(text.encode('utf-16-le'))//2<=1024
                        if not event.get('outcome_message_id'):
                            extra['outcome_message_id']=self.telegram.call('sendAnimation',{'chat_id':event['chat_id'],
                                'animation':Path(__file__).resolve().parent/'assets'/'budget-approved-raccoon.gif',
                                'caption':text if fits else result})['message_id']
                            self.backend.request('POST',endpoint+'/ack',{'lease_token':event['lease_token'],'outcome':'progress',**extra})
                        if not fits:self.telegram.call('sendMessage',{'chat_id':event['chat_id'],'text':row['description']})
                    else:self.telegram.call('sendMessage',{'chat_id':event['chat_id'],'text':text})
                else:
                    row=current['request'];message_id=event.get('detail_message_id')
                    # First sends are passive. Only a durably recorded ID may receive action buttons.
                    if not message_id:
                        message_id=self.telegram.call('sendMessage',{'chat_id':event['chat_id'],'text':budget_text(row)+'\nПодготовка заявки…'})['message_id']
                        extra['detail_message_id']=message_id
                        self.backend.request('POST',endpoint+'/ack',{'lease_token':event['lease_token'],'outcome':'progress',**extra})
                    if current.get('telegram_file_id') and not event.get('photo_message_id'):
                        current=validate()
                        if not current['valid']:
                            outcome='permanent'
                        else:
                            extra['photo_message_id']=self.telegram.call('sendPhoto',{'chat_id':event['chat_id'],
                                'photo':current['telegram_file_id'],'caption':f"Чек / подтверждение заявки №{row['id']}"})['message_id']
                            self.backend.request('POST',endpoint+'/ack',{'lease_token':event['lease_token'],'outcome':'progress',**extra})
                    current=validate()
                    if not current['valid']:
                        self.budget_edit('editMessageReplyMarkup',{'chat_id':event['chat_id'],'message_id':message_id,'reply_markup':{'inline_keyboard':[]}})
                        outcome='permanent'
                    elif outcome!='permanent':
                        row=current['request']
                        def button(label,**data):
                            token=self.store.callback(event['telegram_user_id'],event['generation'],data,persistent=True)
                            return {'text':label,'callback_data':token}
                        payload={'chat_id':event['chat_id'],'message_id':message_id,'text':budget_text(row),'reply_markup':{'inline_keyboard':[
                            [button('Одобрить: наличные',kind='budget_decide',id=row['id'],decision='approved',account='cash',expected_amount=row['amount'])],
                            [button('Одобрить: карта',kind='budget_decide',id=row['id'],decision='approved',account='card',expected_amount=row['amount'])],
                            [button('Не одобрить',kind='budget_decide',id=row['id'],decision='rejected')],
                            [button('Изменить сумму',kind='budget_edit_amount',id=row['id'])]]}}
                        if not self.budget_edit('editMessageText',payload):
                            # Deleted canonical message: retry creates a new passive replacement.
                            replacement=self.telegram.call('sendMessage',{'chat_id':event['chat_id'],'text':budget_text(row)+'\nПодготовка заявки…'})['message_id']
                            extra['detail_message_id']=replacement;outcome='retry'
            except ApiError as error:outcome='permanent' if error.status in {403,404,410} else 'retry'
            except TelegramError as error:
                if error.code==429:self.store.pause_sending(error.retry_after or 30)
                outcome='blocked' if error.code==403 else 'retry'
                if error.retry_after:extra['retry_after']=error.retry_after
            except TransportError:outcome='retry'
            self.backend.request('POST',endpoint+'/ack',{'lease_token':event['lease_token'],'outcome':outcome,**extra})

    def budget_edit(self,method,payload):
        try:self.telegram.call(method,payload);return True
        except TelegramError as error:
            if error.reason=='unchanged':return True
            if error.reason=='missing':return False
            raise


def budget_text(row):
    kind='Возврат денег' if row['request_type']=='reimbursement' else 'Пополнение бюджета'
    text=f"Заявка №{row['id']}: {kind}\n{row['applicant_name']}\nОтдел: {', '.join(row['departments']) or 'не указан'}\n{row['description']}\nСумма: {row['amount']} BYN"
    if row.get('payout_details'):text+='\nРеквизиты: '+row['payout_details']
    if row.get('amount_changes'):text+='\nИсходная сумма: '+row['submitted_amount']+' BYN (сумма исправлена)'
    return text
