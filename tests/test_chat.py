import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chat import Chat
from store import Store
from clients import ApiError


class FakeBackend:
    def __init__(self):
        self.calls=[]
        self.linked=True
        self.generation='generation-1'
        self.preferences={}
        self.can_manage=True
        self.can_delete=True
        self.tasks=[{'id':1,'title':'План','description':'Описание','deadline':'2030-01-02','version':1,
                     'archived':False,'assignees':[],'department_card_ids':[],'event_card_id':None,'completed':False,'assignee_ids':[7],'creator_name':'Иван','creator_id':8,'can_change_status':True,'departments':[{'title':'IT'}],'event':None}]
        self.items=[{'id':2,'name':'Микрофон','description':'Рабочий','version':1,'holder_user':None,
                     'location':{'id':1,'name':'В правке'},'taken_at':None}]
    def request(self,method,path,body=None,*,tid=None,request_id=None,generation=None):
        self.calls.append((method,path,body,tid,request_id))
        if path.split('?')[0]=='bot/me':
            if not self.linked:raise ApiError(403,'Привяжите аккаунт')
            return {'user_id':7,'name':'Анна','generation':self.generation,'login':'anna'}
        if path=='bot/personal-data/me':return {'service':True,'saved_details':True,'policy':{'configured':True,'version':'test'}}
        if path=='bot/payout-details' and method=='GET':return []
        if path=='bot/events':return []
        if path=='bot/claim':return {'pending':True}
        if path.endswith('/preferences/notifications'):
            if method=='PUT':self.preferences[path]=body['enabled']
            return {'enabled':self.preferences.get(path,True)}
        if path=='bot/tasks/options':return {'users':[{'id':7,'name':'Анна','login':'anna'}],'events':[],'departments':[]}
        if path=='bot/tasks?scope=all':return self.tasks
        if path=='bot/tasks?include_created=true&include_completed=true':return self.tasks
        if path=='bot/tasks?include_created=true':return [t for t in self.tasks if not t['completed']]
        if path=='bot/tasks/1':
            if method=='PATCH':self.tasks[0].update(body)
            return self.tasks[0]
        if path=='bot/equipment/options':return {'can_manage':self.can_manage,'can_delete':self.can_delete,'default_location_id':1,
            'users':[{'id':7,'name':'Анна','login':'anna'}], 'locations':[{'id':1,'name':'В правке'}]}
        if path=='bot/equipment':
            if method=='POST':
                self.items.append({'id':3,'name':body['name'],'description':body.get('description',''),'version':1,
                    'holder_user':None,'location':{'id':1,'name':'В правке'},'taken_at':None})
                return self.items[-1]
            return self.items
        if path=='bot/equipment/2/take':return {**self.items[0],'holder_user':{'id':7,'name':'Анна'}}
        if path=='bot/equipment/2/history':return [{'action':'taken','holder_name':'Анна','actor_name':'Анна','taken_at':'2026-11-13T10:00:00','recorded_at':'2026-11-13T10:00:00'}]
        if path=='bot/equipment/2':return self.items[0]
        if path=='bot/equipment/locations':return {'id':4,'name':body['name']}
        if path=='bot/unlink':self.linked=False;return {'unlinked':True}
        raise AssertionError((method,path,body))


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(str(Path(self.temp.name)/'bot.db'))
        self.backend=FakeBackend()
        self.chat=Chat(self.backend,self.store,'https://famcs-legacy.by')
        self.counter=0
    def tearDown(self):self.store.close();self.temp.cleanup()
    def message(self,text,tid=101,chat_type='private'):
        self.counter+=1
        update={'update_id':self.counter,'message':{'from':{'id':tid,'first_name':'Анна'},'chat':{'id':tid,'type':chat_type},'text':text}}
        self.chat.process(update)
        return update
    def replies(self):return self.store.outgoing()
    def button(self,label,tid=101):
        for out in reversed(self.replies()):
            for row in out['payload'].get('reply_markup',{}).get('inline_keyboard',[]):
                for button in row:
                    if label in button.get('text',''):
                        self.counter+=1
                        update={'update_id':self.counter,'callback_query':{'id':'q'+str(self.counter),'from':{'id':tid},
                          'message':{'chat':{'id':tid,'type':'private'}},'data':button['callback_data']}}
                        self.chat.process(update)
                        return update
        raise AssertionError('Button not found: '+label)

    def test_creator_can_control_notifications_and_reopen_completed_task(self):
        self.backend.tasks[0].update(assignee_ids=[],creator_id=7)
        self.message('/tasks')
        self.button('Отключить все уведомления')
        self.assertFalse(self.backend.preferences['bot/tasks/preferences/notifications'])
        self.button('План')
        self.assertIn('Создатель: Иван', self.replies()[-1]['payload']['text'])
        self.button('Отключить уведомления по таске')
        self.assertFalse(self.backend.preferences['bot/tasks/1/preferences/notifications'])
        self.button('Включить уведомления по таске')
        self.assertTrue(self.backend.preferences['bot/tasks/1/preferences/notifications'])

        self.button('Выполнено')
        self.message('/tasks')
        self.button('Выполненные таски')
        self.button('План')
        self.button('Вернуть в работу')
        self.assertFalse(self.backend.tasks[0]['completed'])
        self.assertIn('Таска возвращена в работу.',self.replies()[-1]['payload']['text'])

    def test_archived_task_status_is_read_only(self):
        self.backend.tasks[0].update(archived=True,can_change_status=False)
        self.message('/tasks')
        self.button('План')
        payload=self.replies()[-1]['payload']
        self.assertIn('В архиве',payload['text'])
        self.assertNotIn('Выполнено',str(payload['reply_markup']))
        self.assertNotIn('Вернуть в работу',str(payload['reply_markup']))

    def test_section_commands_always_open_distinct_menus_including_binding_links(self):
        self.store.menu_sent(-1,101,37)
        sessions=set()
        for command in ['/start','/start','/start link_'+'x'*43,'/tasks','/tasks','/equipment','/zones','/link','/help','/unlink','/cancel']:
            self.message(command)
            session=self.replies()[-1]['payload']['_new_menu']
            self.assertNotIn(session,sessions)
            sessions.add(session)

    def test_text_input_updates_the_menu_where_the_form_was_started(self):
        self.store.menu_sent(-1,101,80)
        token=self.store.callback(101,self.backend.generation,{'kind':'task_new','menu':True})
        self.chat.process({'update_id':71,'callback_query':{'id':'q','from':{'id':101},
            'message':{'message_id':40,'chat':{'id':101,'type':'private'}},'data':token}})
        self.message('Новая таска')
        self.assertEqual(self.replies()[-1]['payload']['_menu_message_id'],40)
        self.assertNotIn('_new_menu',self.replies()[-1]['payload'])

    def test_task_draft_validates_deadline_preserves_version_and_cancels(self):
        self.message('/tasks')
        self.button('Все доступные таски')
        self.button('План')
        self.button('Редактировать таску')
        self.button('Дедлайн')
        self.message('31.02.2027')
        self.assertIn('ДД.ММ.ГГГГ',self.replies()[-1]['payload']['text'])
        self.message('01.12.2027')
        self.button('Сохранить изменения')
        mutation=next(c for c in self.backend.calls if c[0]=='PATCH')
        self.assertEqual(mutation[2],{'version':1,'deadline':'2027-12-01'})
        self.message('/tasks')
        self.button('Создать таску')
        self.message('Отменённая')
        self.message('/cancel')
        self.assertFalse(any(c[0]=='POST' and c[1]=='bot/tasks' for c in self.backend.calls))

    def test_group_updates_do_not_read_member_data(self):
        self.message('/tasks',chat_type='group')
        self.assertEqual(self.backend.calls,[])
        self.assertEqual(self.replies(),[])

    def test_unbound_contact_is_not_trusted_and_link_claim_waits_for_website(self):
        self.backend.linked=False
        self.message('/tasks')
        self.assertIn('Привязать',str(self.replies()))
        self.message('/start link_'+'x'*43)
        claims=[c for c in self.backend.calls if c[1]=='bot/claim']
        self.assertEqual(claims[0][2]['telegram_user_id'],101)
        self.assertIn('подтверд',str(self.replies()).lower())
        self.assertFalse(self.backend.linked)

    def test_own_task_completion_repeated_callback_is_one_mutation(self):
        self.message('/tasks')
        self.button('План')
        update=self.button('Выполнено')
        self.chat.process(update)
        update['update_id']=999
        self.chat.process(update)
        mutations=[c for c in self.backend.calls if c[0]=='PATCH']
        self.assertEqual(len(mutations),1)
        self.assertEqual(mutations[0][2],{'version':1,'completed':True})
        self.assertTrue(self.backend.tasks[0]['completed'])

    def test_callback_tokens_are_owner_and_binding_bound(self):
        self.message('/tasks')
        self.button('План')
        self.button('Выполнено',tid=202)
        self.assertFalse(self.backend.tasks[0]['completed'])
        self.backend.generation='new-binding'
        self.button('Выполнено')
        self.assertFalse(self.backend.tasks[0]['completed'])

    def test_equipment_create_cancel_take_and_history(self):
        self.message('/equipment')
        self.button('Добавить')
        self.message('Проектор')
        self.message('Для лекций')
        self.button('Создать')
        self.assertEqual(self.backend.items[-1]['name'],'Проектор')
        self.assertEqual(len([c for c in self.backend.calls if c[0]=='POST' and c[1]=='bot/equipment']),1)
        self.message('/equipment')
        self.button('Добавить')
        self.message('Не сохранять')
        self.message('/cancel')
        self.assertIsNone(self.store.state(101))
        self.message('/equipment')
        self.button('Микрофон')
        self.button('Взял предмет')
        self.assertTrue(any(c[1]=='bot/equipment/2/take' for c in self.backend.calls))
        self.button('История')
        self.assertIn('Анна',str(self.replies()[-1]))

    def test_read_only_equipment_hides_management_and_late_role_loss_is_checked(self):
        self.backend.can_manage=False;self.backend.can_delete=False
        self.message('/equipment')
        self.button('Микрофон')
        keyboard=self.replies()[-1]['payload']['reply_markup']
        self.assertNotIn('Редактировать',str(keyboard))
        self.assertNotIn('Удалить',str(keyboard))

    def test_drafts_and_offsets_survive_restart(self):
        self.message('/equipment');self.button('Добавить');self.message('Проектор')
        path=self.store.path;self.store.close()
        self.store=Store(path);self.chat=Chat(self.backend,self.store,'https://famcs-legacy.by')
        self.message('/skip');self.button('Создать')
        self.assertEqual(self.backend.items[-1]['name'],'Проектор')
        self.assertEqual(self.store.offset(),self.counter+1)

    def test_message_utf16_limits_and_no_markup_injection(self):
        self.backend.tasks[0]['description']='😀'*10000+'<script>x</script>'
        self.message('/tasks');self.button('План')
        for out in self.replies():
            if 'text' in out['payload']:
                self.assertLessEqual(len(out['payload']['text'].encode('utf-16-le'))//2,4096)
                self.assertNotIn('parse_mode',out['payload'])

    def test_network_wait_does_not_hold_bot_sqlite_write_lock(self):
        import threading
        original=self.backend.request
        completed=threading.Event()
        def slow(method,path,body=None,**kwargs):
            if path=='bot/equipment':
                thread=threading.Thread(target=lambda: (self.store.enqueue('sendMessage',{'chat_id':202,'text':'parallel delivery'}),completed.set()))
                thread.start()
                self.assertTrue(completed.wait(1), 'Backend network calls must not hold bot SQLite write reservations')
                thread.join()
            return original(method,path,body,**kwargs)
        self.backend.request=slow
        self.message('/equipment')
