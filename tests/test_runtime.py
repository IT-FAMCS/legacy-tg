import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from clients import BackendClient, TelegramClient, TelegramError, TransportError
from store import Store
from worker import DeliveryWorker
from runtime import Runtime

class TransportTests(unittest.TestCase):
    def test_telegram_result_objects_and_rate_limit_are_supported_without_secret_errors(self):
        client=TelegramClient('123:private-token')
        with patch('clients.urlopen',return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":1}}')):
            self.assertEqual(client.call('sendMessage',{})['message_id'],1)
        with patch('clients.urlopen',return_value=io.BytesIO(b'{"ok":false,"error_code":429,"description":"123:private-token","parameters":{"retry_after":45}}')):
            with self.assertRaises(TelegramError) as error:client.call('sendMessage',{})
            self.assertEqual(error.exception.retry_after,45)
            self.assertNotIn('private-token',str(error.exception))
        with patch('clients.urlopen',side_effect=URLError('https://secret')):
            with self.assertRaises(TransportError) as error:client.call('getUpdates',{})
            self.assertNotIn('secret',str(error.exception))
    def test_public_plaintext_backend_and_short_service_keys_rejected(self):
        for url in ['http://example.com','https://user:pass@example.com','https://example.com/?key=secret']:
            with self.assertRaises(ValueError):BackendClient(url,'x'*40)
        with self.assertRaises(ValueError):BackendClient('https://example.com','short')

class FakeTelegram:
    def __init__(self):self.calls=[];self.error=None
    def call(self,method,payload):
        self.calls.append((method,payload))
        if self.error:raise self.error
        return {'message_id':1}

class FakeBackend:
    def __init__(self):
        self.calls=[];self.generation='old';self.rows=[]
    def request(self,method,path,body=None,**kwargs):
        self.calls.append((method,path,body,kwargs))
        if path.startswith('bot/me'):return {'user_id':101,'generation':self.generation,'blocked':False}
        if path.startswith('bot/tasks/'):return {'creator_id':102,'assignee_ids':[101],'completed':False,'archived':False}
        if path=='service/notifications/claim':rows=self.rows;self.rows=[];return rows
        if path.endswith('/validate'):return {'valid':True,'text':'Current notification'}
        if path.endswith('/ack'):return {'state':'delivered'}
        return {}

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(str(Path(self.temp.name)/'bot.db'))
        self.telegram=FakeTelegram();self.backend=FakeBackend();self.worker=DeliveryWorker(self.backend,self.telegram,self.store)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_menu_remembers_message_after_restart_and_replaces_deleted_message(self):
        def screen(label):
            self.store.enqueue('menu',{'chat_id':101,'text':label},101)
            self.worker.flush_replies()
        screen('one')
        self.store=Store(self.store.path)
        self.worker=DeliveryWorker(self.backend,self.telegram,self.store)
        screen('two')
        self.assertEqual(self.telegram.calls[-1],('editMessageText',{'chat_id':101,'message_id':1,'text':'two'}))
        original=self.telegram.call
        def missing(method,payload):
            if method=='editMessageText':
                raise TelegramError(400,reason='missing')
            return original(method,payload)
        self.telegram.call=missing
        screen('three')
        self.assertEqual(self.telegram.calls[-1][0],'sendMessage')
        self.assertEqual(self.store.outgoing(),[])

    def test_inflight_menu_send_and_notification_keep_new_screen_address(self):
        self.store.enqueue('menu',{'chat_id':101,'text':'first'},101)
        original=self.telegram.call
        def interleaved(method,payload):
            original(method,payload)
            self.store.enqueue('menu',{'chat_id':101,'text':'latest'},101)
            return {'message_id':41}
        self.telegram.call=interleaved
        self.worker.flush_replies()
        self.telegram.call=original
        self.backend.rows=[{'id':2,'lease_token':'lease','chat_id':101,'telegram_user_id':101,
                            'generation':'old','task_id':7,'kind':'updated','text':'edited'}]
        self.worker.notifications()
        self.worker.flush_replies()
        self.assertEqual([method for method,_ in self.telegram.calls],['sendMessage','sendMessage','editMessageText'])
        self.assertEqual(self.telegram.calls[-1][1]['message_id'],41)
        self.assertEqual(self.telegram.calls[-1][1]['text'],'latest')

    def test_unchanged_edit_is_acknowledged_without_sending_again(self):
        for _ in range(2):
            self.store.enqueue('menu',{'chat_id':101,'text':'one'},101)
            self.worker.flush_replies()
            self.telegram.error=TelegramError(400,reason='unchanged')
        self.assertEqual(self.store.outgoing(),[])
        self.assertEqual([method for method,_ in self.telegram.calls],['sendMessage','editMessageText'])

    def test_new_menu_supersedes_delayed_screen(self):
        self.store.enqueue('menu',{'chat_id':101,'text':'old'},101)
        self.store.retry(self.store.outgoing()[0]['id'],60)
        self.store.enqueue('menu',{'chat_id':101,'text':'new'},101)
        self.worker.flush_replies()
        self.assertEqual(self.telegram.calls,[('sendMessage',{'chat_id':101,'text':'new'})])
        self.assertEqual(self.store.outgoing(),[])

    def test_outgoing_old_binding_is_dropped_without_sending(self):
        self.store.enqueue('sendMessage',{'chat_id':101,'text':'private data'},101,'old')
        self.backend.generation='new';self.worker.flush_replies()
        self.assertEqual(self.telegram.calls,[])
        self.assertEqual(self.store.outgoing(),[])
    def test_notification_send_ack_and_429_retry_are_lease_scoped(self):
        row={'id':1,'lease_token':'lease','chat_id':101,'telegram_user_id':101,'generation':'old','task_id':7,'kind':'assigned','text':'Assigned'}
        self.backend.rows=[row];self.worker.notifications()
        self.assertEqual(self.telegram.calls[0][0],'sendMessage')
        self.assertEqual(self.backend.calls[-1][2]['outcome'],'delivered')
        self.backend.rows=[row];self.telegram.error=TelegramError(429,60)
        self.worker.notifications()
        self.assertEqual(self.backend.calls[-1][2],{'lease_token':'lease','outcome':'retry','retry_after':60})
    def test_runtime_refuses_existing_webhook_without_deleting_it(self):
        class Telegram(FakeTelegram):
            def call(self,method,payload):self.calls.append((method,payload));return {'url':'https://existing'}
        telegram=Telegram()
        runtime=Runtime(self.backend,telegram,self.store,'https://example.com')
        with self.assertRaises(RuntimeError):runtime.check_startup()
        self.assertFalse(any(method=='deleteWebhook' for method,_ in telegram.calls))

    def test_flood_wait_pauses_later_replies_and_notifications(self):
        self.store.enqueue('sendMessage',{'chat_id':101,'text':'first'})
        self.store.enqueue('sendMessage',{'chat_id':101,'text':'second'})
        self.telegram.error=TelegramError(429,60)
        self.worker.flush_replies()
        self.assertEqual(len(self.telegram.calls),1)
        self.worker.notifications()
        self.assertEqual(self.backend.calls,[])

    def test_external_button_opens_new_menu_and_navigation_edits_it(self):
        from chat import Chat
        from test_chat import FakeBackend as ChatBackend
        backend = ChatBackend()
        backend.generation = self.backend.generation
        chat = Chat(backend, self.store, 'https://example.com')
        self.store.menu_sent(-1, 101, 50)
        token = self.store.callback(101, backend.generation, {'kind': 'task', 'id': 1, 'new_menu': True})
        def click(token, source_id, update_id):
            chat.process({'update_id': update_id, 'callback_query': {
                'id': str(update_id), 'from': {'id': 101},
                'message': {'message_id': source_id, 'chat': {'id': 101, 'type': 'private'}},
                'data': token}})
            self.worker.flush_replies()
        click(token, 80, 1)
        calls = [call for call in self.telegram.calls if call[0] != 'answerCallbackQuery']
        self.assertEqual([method for method, _ in calls], ['sendMessage'])
        self.assertIn(backend.tasks[0]['title'], calls[0][1]['text'])
        self.assertNotIn('_new_menu', calls[0][1])
        self.assertEqual(self.store.menu_message(101), 1)
        back = calls[0][1]['reply_markup']['inline_keyboard'][-1][0]['callback_data']
        click(back, 1, 2)
        self.assertEqual(self.telegram.calls[-1][0], 'editMessageText')
        self.assertEqual(self.telegram.calls[-1][1]['message_id'], 1)
        click(token, 80, 3)
        self.assertEqual(self.telegram.calls[-1][0], 'sendMessage')

    def test_navigation_edits_clicked_old_menu(self):
        self.store.menu_sent(-1, 101, 99)
        self.store.enqueue('menu', {'chat_id': 101, 'text': 'old menu', '_menu_message_id': 50}, 101)
        self.worker.flush_replies()
        self.assertEqual(self.telegram.calls[-1][0], 'editMessageText')
        self.assertEqual(self.telegram.calls[-1][1]['message_id'], 50)
        self.assertNotIn('_menu_message_id', self.telegram.calls[-1][1])
        self.assertEqual(self.store.menu_message(101), 50)

    def test_delayed_new_menu_keeps_new_message_when_screen_is_superseded(self):
        self.store.menu_sent(-1, 101, 99)
        self.store.enqueue('menu', {'chat_id': 101, 'text': 'initial', '_new_menu': True}, 101)
        self.store.enqueue('menu', {'chat_id': 101, 'text': 'latest'}, 101)
        self.worker.flush_replies()
        self.assertEqual(self.telegram.calls[-1][0], 'sendMessage')
        self.assertEqual(self.telegram.calls[-1][1]['text'], 'latest')

    def test_notification_is_dropped_when_recipient_is_no_longer_a_participant(self):
        request=self.backend.request
        def changed(method,path,body=None,**kwargs):
            if path.endswith('/validate'):
                return {'valid':False}
            return request(method,path,body,**kwargs)
        self.backend.request=changed
        self.backend.rows=[{'id':1,'lease_token':'lease','chat_id':101,'telegram_user_id':101,
            'generation':'old','task_id':7,'kind':'updated','text':'private changes'}]
        self.worker.notifications()
        self.assertEqual(self.telegram.calls,[])
        self.assertEqual(self.backend.calls[-1][2]['outcome'],'permanent')

    def test_distinct_section_commands_each_send_a_menu(self):
        for session in ('update:1','update:2'):
            self.store.enqueue('menu', {'chat_id':101,'text':session,'_new_menu':session}, 101)
        self.worker.flush_replies()
        self.assertEqual([method for method,_ in self.telegram.calls], ['sendMessage','sendMessage'])

    def test_inflight_new_menu_screen_uses_the_sent_message(self):
        self.store.enqueue('menu', {'chat_id':101,'text':'first','_new_menu':'update:1'}, 101)
        original=self.telegram.call
        def interleaved(method,payload):
            original(method,payload)
            self.store.enqueue('menu', {'chat_id':101,'text':'latest'}, 101)
            return {'message_id':41}
        self.telegram.call=interleaved
        self.worker.flush_replies()
        self.telegram.call=original
        self.worker.flush_replies()
        self.assertEqual([method for method,_ in self.telegram.calls], ['sendMessage','editMessageText'])
        self.assertEqual(self.telegram.calls[-1][1]['message_id'],41)

    def test_notification_navigation_survives_expiry_and_keeps_binding_guard(self):
        self.backend.rows=[{'id':1,'lease_token':'lease','chat_id':101,'telegram_user_id':101,
            'generation':'old','task_id':7,'kind':'updated','text':'change'}]
        self.worker.notifications()
        token=self.telegram.calls[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        with patch('store.time.time',return_value=9999999999):
            self.store.finish(1)
            entry=self.store.action(token,101,'old')
            self.assertEqual(entry['data']['id'],7)
            self.assertTrue(entry['data']['new_menu'])
            self.assertIsNone(self.store.action(token,101,'new'))
            self.assertIsNone(self.store.action(token,102,'old'))
