import tempfile
import unittest
from pathlib import Path
from chat import Chat
from store import Store
from clients import ApiError
from test_chat import FakeBackend

class Backend(FakeBackend):
    def __init__(self):
        super().__init__(); self.requests=[]; self.unavailable=False; self.commit_then_fail=False; self.receipts={}
    def request(self,method,path,body=None,**kw):
        if path=='bot/budget/requests':
            if self.unavailable: raise ApiError(409,'No cashier')
            key=kw.get('request_id')
            if key in self.receipts:return self.receipts[key]
            self.requests.append(body); self.receipts[key]={'id':len(self.requests)}
            if self.commit_then_fail:
                self.commit_then_fail=False;raise ApiError(503,'proxy timeout after commit')
            return self.receipts[key]
        return super().request(method,path,body,**kw)

class BudgetChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(str(Path(self.temp.name)/'bot.db'))
        self.backend=Backend(); self.chat=Chat(self.backend,self.store,'https://example.org'); self.counter=0
    def tearDown(self): self.store.close(); self.temp.cleanup()
    def message(self,text='',photo=None):
        self.counter+=1
        msg={'from':{'id':101},'chat':{'id':101,'type':'private'},'text':text}
        if photo: msg['photo']=photo
        self.chat.process({'update_id':self.counter,'message':msg})
    def click(self,label):
        for row in reversed(self.store.outgoing()):
            for buttons in row['payload'].get('reply_markup',{}).get('inline_keyboard',[]):
                for button in buttons:
                    if label in button['text']:
                        self.counter+=1
                        self.chat.process({'update_id':self.counter,'callback_query':{'id':str(self.counter),'from':{'id':101},'message':{'chat':{'id':101,'type':'private'}},'data':button['callback_data']}})
                        return
        self.fail('Missing button '+label)
    def test_receipt_wizard_and_failed_submit_preserves_draft(self):
        self.message('/budget'); self.click('возврат')
        self.message('Supplies'); self.message('1.234')
        self.assertEqual(self.store.state(101)['step'],'amount')
        self.message('12,34'); self.message('card 123')
        self.message('not a photo')
        self.assertEqual(self.store.state(101)['step'],'photo')
        self.message(photo=[{'file_id':'small'},{'file_id':'receipt'}])
        self.backend.unavailable=True; self.click('Подтвердить')
        self.assertIsNotNone(self.store.state(101))
        self.backend.unavailable=False; self.click('Подтвердить')
        self.assertEqual(self.backend.requests[0]['telegram_file_id'],'receipt')
        self.assertEqual(self.backend.requests[0]['amount'],'12.34')
        self.assertIsNone(self.store.state(101))
    def test_add_funds_requires_photo_and_cancel(self):
        self.message('/budget'); self.click('пополнение'); self.message('Donation'); self.message('5'); self.message('/skip')
        self.assertEqual(self.store.state(101)['step'],'photo')
        self.assertEqual(self.backend.requests,[])
        self.message(photo=[{'file_id':'receipt'}]); self.click('Подтвердить')
        self.assertEqual(self.backend.requests[0]['request_type'],'add_funds')
        self.assertEqual(self.backend.requests[0]['telegram_file_id'],'receipt')
        self.message('/budget'); self.click('возврат'); self.message('/cancel')
        self.assertIsNone(self.store.state(101))

    def test_initial_message_has_no_buttons_before_durable_progress_ack(self):
        from worker import DeliveryWorker
        from clients import TransportError
        event={'id':1,'lease_token':'lease','event_type':'request','telegram_user_id':101,'chat_id':101,'generation':'old',
               'detail_message_id':None,'photo_message_id':None,'telegram_file_id':None,
               'request':{'id':1,'request_type':'reimbursement','applicant_name':'Test','departments':[],'description':'Supplies','amount':'12.34','submitted_amount':'12.34','payout_details':'card','amount_changes':[]}}
        class Backend:
            def request(self,method,path,body=None,**kw):
                if path.endswith('/claim'):return [event]
                if path=='bot/me':return {'generation':'old'}
                if path.endswith('/validate'):return {'valid':True,**event}
                if body.get('outcome')=='progress':raise TransportError('offline')
                return {'ok':True}
        class Telegram:
            def __init__(self):self.calls=[]
            def call(self,method,payload):self.calls.append((method,payload));return {'message_id':42}
        telegram=Telegram();DeliveryWorker(Backend(),telegram,self.store).budget_notifications()
        self.assertEqual(telegram.calls[0][0],'sendMessage')
        self.assertFalse(telegram.calls[0][1].get('reply_markup',{}).get('inline_keyboard'))
        self.assertFalse(any(method=='editMessageText' for method,_ in telegram.calls))

    def test_confirm_retry_after_proxy_failure_reuses_draft_idempotency_key(self):
        self.message('/budget');self.click('пополнение');self.message('Donation');self.message('5');self.message(photo=[{'file_id':'receipt'}])
        self.backend.commit_then_fail=True;self.click('Подтвердить');self.click('Подтвердить')
        self.assertEqual(len(self.backend.requests),1)

    def test_approved_outcome_attaches_bundled_raccoon_and_preserves_long_description(self):
        from worker import DeliveryWorker
        owner=self
        for status in ['approved','rejected']:
            for description in ['Supplies','Расходы '*300]:
                row={'id':1,'status':status,'amount':'12.34','description':description}
                class Backend:
                    def request(self,method,path,body=None,**kw):
                        if path.endswith('/claim'):return [{'id':1,'lease_token':'lease','event_type':'outcome','chat_id':101}]
                        if path.endswith('/validate'):return {'valid':True,'request':row}
                        return {'ok':True}
                class Telegram:
                    def __init__(self):self.calls=[]
                    def call(self,method,payload):self.calls.append((method,payload));return {'message_id':7}
                telegram=Telegram();DeliveryWorker(Backend(),telegram,self.store).budget_notifications()
                if status=='approved':
                    animation=next(p for method,p in telegram.calls if method=='sendAnimation')
                    self.assertTrue(Path(animation['animation']).is_file());self.assertIn('12.34',animation['caption'])
                    self.assertLessEqual(len(animation['caption'].encode('utf-16-le'))//2,1024)
                else:self.assertFalse(any(method=='sendAnimation' for method,_ in telegram.calls))
                texts=''.join(p.get('text',p.get('caption','')) for _,p in telegram.calls)
                self.assertIn(description,texts)

    def test_long_approval_retry_does_not_resend_successful_animation(self):
        from worker import DeliveryWorker
        from clients import TelegramError
        row={'id':1,'status':'approved','amount':'12.34','description':'Расходы '*200}
        class Backend:
            def __init__(self):self.progress=None
            def request(self,method,path,body=None,**kw):
                if path.endswith('/claim'):return [{'id':1,'lease_token':'lease','event_type':'outcome','chat_id':101,'outcome_message_id':self.progress}]
                if path.endswith('/validate'):return {'valid':True,'request':row}
                if body.get('outcome')=='progress':self.progress=body.get('outcome_message_id')
                return {'ok':True}
        class Telegram:
            def __init__(self):self.calls=[];self.failed=False
            def call(self,method,payload):
                if method=='sendMessage' and not self.failed:
                    self.failed=True;raise TelegramError(429,retry_after=1)
                self.calls.append((method,payload));return {'message_id':77}
        backend=Backend();telegram=Telegram();worker=DeliveryWorker(backend,telegram,self.store)
        worker.budget_notifications()
        from unittest.mock import patch
        with patch.object(self.store,'sending_allowed',return_value=True):worker.budget_notifications()
        self.assertEqual(sum(method=='sendAnimation' for method,_ in telegram.calls),1)
        self.assertTrue(any(method=='sendMessage' for method,_ in telegram.calls))

    def test_budget_input_sends_distinct_messages_even_after_validation_error(self):
        self.store.menu_sent(-1,101,80)
        self.message('/budget');self.click('возврат')
        sessions=[]
        for text in ['Supplies','invalid','12','account 123']:
            self.message(text)
            payload=self.store.outgoing()[-1]['payload']
            self.assertIn('_new_menu',payload)
            self.assertNotIn('_menu_message_id',payload)
            sessions.append(payload['_new_menu'])
        self.assertEqual(len(set(sessions)),len(sessions))

    def test_old_review_draft_without_receipt_cannot_submit(self):
        self.message('/budget');self.click('пополнение');self.message('Donation');self.message('5')
        state=self.store.state(101);state['step']='review'
        self.store.set_state(101,self.backend.generation,state)
        token=self.store.callback(101,self.backend.generation,{'kind':'budget_submit','menu':True,'flow':state['flow']})
        self.counter+=1
        self.chat.process({'update_id':self.counter,'callback_query':{'id':str(self.counter),'from':{'id':101},'message':{'chat':{'id':101,'type':'private'}},'data':token}})
        self.assertEqual(self.backend.requests,[])
        self.assertEqual(self.store.state(101)['step'],'photo')
