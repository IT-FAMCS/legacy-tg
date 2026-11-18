"""Run explicitly with the backend environment; no real Telegram I/O.
python tests/integration_backend.py --backend-root ../tg-back
"""
import argparse
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--backend-root',required=True)
args,remaining=parser.parse_known_args()
backend_root=Path(args.backend_root).resolve()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(backend_root));sys.path.insert(0,str(backend_root/'tests'))
import test_telegram as identity
from test_trainee_access import asgi_request
import models
import database
from chat import Chat
from clients import ApiError
from store import Store
from worker import DeliveryWorker

class Backend:
    def request(self,method,path,body=None,*,tid=None,request_id=None,generation=None):
        headers={'X-Telegram-Service-Key':identity.KEY}
        if tid is not None:headers['X-Telegram-User-ID']=tid
        if request_id is not None:headers['X-Telegram-Request-ID']=request_id
        if generation:headers['X-Telegram-Binding']=generation
        status,data=asyncio.run(asgi_request(method,'/api/telegram/'+path,body,extra_headers=headers))
        if status>=400:raise ApiError(status,str(data.get('detail','Error')))
        return data

class Telegram:
    def __init__(self):self.sent=[]
    def call(self,method,payload):self.sent.append((method,payload));return {'message_id':len(self.sent)}

class IntegrationTests(unittest.TestCase):
    web=identity.TelegramTests.web;bot=identity.TelegramTests.bot;issue=identity.TelegramTests.issue
    bind=identity.TelegramTests.bind;create_task=identity.TelegramTests.create_task
    @classmethod
    def tearDownClass(cls):database.engine.dispose()
    def setUp(self):
        identity.TelegramTests.setUp(self)
        self.bind();self.bind(tid=202,role='Заместитель председателя по IT')
        self.temp=tempfile.TemporaryDirectory();self.store=Store(str(Path(self.temp.name)/'bot.db'))
        self.backend=Backend();self.chat=Chat(self.backend,self.store,'https://famcs-legacy.by')
        self.counter=0;self.tid=202
    def tearDown(self):
        self.store.close();self.temp.cleanup();identity.TelegramTests.tearDown(self)
    def message(self,text,tid=None):
        self.tid=tid or self.tid;self.counter+=1
        self.chat.process({'update_id':self.counter,'message':{'from':{'id':self.tid},'chat':{'id':self.tid,'type':'private'},'text':text}})
    def button_token(self,text):
        for row in reversed(self.store.outgoing()):
            if row['tid']!=self.tid:continue
            for buttons in row['payload'].get('reply_markup',{}).get('inline_keyboard',[]):
                for button in buttons:
                    if text in button['text']:
                        return button['callback_data']
        self.fail('Missing button '+text+'; '+str(self.store.outgoing()[-2:]))

    def click(self,token):
        self.counter+=1
        self.chat.process({'update_id':self.counter,'callback_query':{'id':str(self.counter),'from':{'id':self.tid},
            'message':{'chat':{'id':self.tid,'type':'private'}},'data':token}})

    def button(self,text):
        self.click(self.button_token(text))

    def test_full_equipment_lifecycle_uses_real_permissions_and_history(self):
        self.message('/equipment');self.button('Добавить предмет');self.message('Проектор');self.message('Для лекций')
        self.button('Выбрать пользователя');self.button('Участник Имя')
        self.button('Дата взятия');self.message('20.11.2026 15:30');self.button('Создать')
        self.db.expire_all();item=self.db.query(models.EquipmentItem).one()
        self.assertEqual(item.holder_user_id,self.users['Участник'])
        self.assertEqual(item.taken_at.hour,12)
        self.button('Редактировать');self.button('Название');self.message('Проектор XL');self.button('Сохранить')
        self.db.expire_all();self.assertEqual(self.db.get(models.EquipmentItem,item.id).name,'Проектор XL')
        self.button('Передать / место');self.button('К изменениям');self.button('Добавить место');self.message('Склад');self.button('Сохранить')
        self.db.expire_all();item=self.db.get(models.EquipmentItem,item.id)
        self.assertIsNone(item.holder_user_id);self.assertEqual(item.location.name,'Склад');self.assertIsNone(item.taken_at)
        self.message('/equipment',tid=101);self.button('Проектор XL');self.button('Взял предмет');self.button('История')
        self.db.expire_all();self.assertEqual(self.db.get(models.EquipmentItem,item.id).holder_user_id,self.users['Участник'])
        self.assertIn('Передан',str(self.store.outgoing()[-1]))
        self.message('/equipment',tid=202);self.button('Проектор XL');self.button('Удалить предмет');self.button('Удалить')
        self.db.expire_all();self.assertIsNotNone(self.db.get(models.EquipmentItem,item.id).deleted_at)
        self.assertEqual(self.db.query(models.EquipmentHistory).filter_by(item_id=item.id).count(),4)

    def test_task_creation_and_edit_by_another_viewer_match_site_rights(self):
        self.message('/tasks',tid=101)
        self.button('Создать таску')
        self.message('Проверить зал')
        self.button('Описание');self.message('До открытия дверей')
        self.button('Дедлайн');self.message('01.12.2027')
        self.button('Ответственные');self.button('Поиск');self.message('Другой участник')
        self.button('Другой участник');self.button('Готово')
        self.button('Отделы');self.button('IT');self.button('Готово')
        self.button('Мероприятие');self.button('Посвящение')
        save=self.button_token('Создать таску')
        self.click(save);self.click(save)
        task=self.db.query(models.Task).one()
        self.assertEqual(task.title,'Проверить зал')
        self.assertEqual(task.deadline,'2027-12-01')
        self.assertEqual(task.description,'До открытия дверей')
        self.assertEqual(task.creator_id,self.users['Участник'])
        self.assertEqual([row.user_id for row in task.assignee_links],[self.users['Другой участник']])
        self.assertEqual(task.event_card_id,self.cards['Посвящение'])
        self.assertEqual([row.card_id for row in task.department_links],[self.cards['IT']])
        self.message('/tasks',tid=202)
        self.assertNotIn(task.title,str(self.store.outgoing()[-1]['payload']))
        self.button('Все доступные таски');self.button('Проверить зал')
        self.assertNotIn('Выполнено',str(self.store.outgoing()[-1]['payload']['reply_markup']))
        self.button('Редактировать таску');self.button('Название');self.message('Подготовить зал')
        self.button('Дедлайн');self.message('/clear')
        self.button('Отделы');self.button('Очистить выбор');self.button('Готово')
        self.button('Сохранить изменения')
        self.db.expire_all()
        self.assertEqual(task.title,'Подготовить зал')
        self.assertIsNone(task.deadline)
        self.assertEqual(task.department_links,[])
        self.assertEqual(task.description,'До открытия дверей')
        self.assertEqual(task.version,2)

    def test_own_task_completion_and_notification_assignment_removal(self):
        author='Заместитель председателя по IT'
        status,task=self.web('POST','/api/tasks',{'title':'Таска для Telegram','assignee_ids':[self.users['Участник']]},role=author)
        self.assertEqual(status,201,task)
        telegram=Telegram();worker=DeliveryWorker(self.backend,telegram,self.store)
        worker.notifications();self.assertIn('назначена',telegram.sent[-1][1]['text'])
        self.web('PATCH',f"/api/tasks/{task['id']}",{'version':1,'assignee_ids':[]},role=author)
        worker.notifications();self.assertIn('сняли',telegram.sent[-1][1]['text'])
        status,task=self.web('PATCH',f"/api/tasks/{task['id']}",{'version':2,'assignee_ids':[self.users['Участник']]},role=author)
        self.message('/tasks',tid=101);self.button('Таска для Telegram');self.button('Выполнено')
        self.db.expire_all();self.assertTrue(self.db.get(models.Task,task['id']).completed)
        self.assertEqual(self.db.query(models.TaskHistory).filter_by(task_id=task['id']).count(),4)

        self.message('/tasks',tid=202)
        self.button('Выполненные таски')
        self.button('Таска для Telegram')
        self.button('Отключить уведомления по таске')
        self.button('Вернуть в работу')
        self.db.expire_all()
        self.assertFalse(self.db.get(models.Task,task['id']).completed)
        self.assertFalse(self.bot('GET',f"bot/tasks/{task['id']}/preferences/notifications",tid=202)[1]['enabled'])

    def test_role_revoked_mid_wizard_and_cancelled_callback_do_not_mutate(self):
        self.message('/equipment');self.button('Добавить предмет');self.message('Не сохранять');self.message('/skip')
        stale=self.button_token('Создать')
        self.message('/cancel');self.click(stale)
        self.assertEqual(self.db.query(models.EquipmentItem).count(),0)
        self.message('/equipment');self.button('Добавить предмет');self.message('Запрещённый');self.message('/skip')
        user=self.db.get(models.User,self.users['Заместитель председателя по IT'])
        user.position_id=self.db.get(models.User,self.users['Участник']).position_id;self.db.commit()
        self.button('Создать');self.assertEqual(self.db.query(models.EquipmentItem).count(),0)

    def test_event_zones_in_chat_assignment_counts_and_clear(self):
        self.db.get(models.User,self.users['Участник']).telegram='@anna'
        self.db.commit()
        self.message('/zones',tid=101);self.button('Посвящение');self.button('Добавить зону')
        self.message('Сцена');self.message('2');self.button('Создать зону')
        self.button('Назначить главного');self.button('Участник Имя')
        self.button('Помогаторы');self.button('Искать человека');self.message('Другой участник');self.button('Другой участник Имя');self.button('Сохранить помогаторов')
        board=self.web('GET',f"/api/cards/{self.cards['Посвящение']}/responsibility-zones")[1]
        self.assertEqual(board['closed'],1)
        self.assertEqual(board['zones'][0]['lead_user_id'],self.users['Участник'])
        self.assertIn('@anna',str(self.store.outgoing()))
        self.button('К зонам мероприятия');self.button('Очистить ответственных');self.button('Очистить все назначения')
        board=self.web('GET',f"/api/cards/{self.cards['Посвящение']}/responsibility-zones")[1]
        self.assertEqual(board['closed'],0);self.assertEqual(len(board['zones']),1)
        self.assertIsNone(board['zones'][0]['lead']);self.assertEqual(board['zones'][0]['helpers'],[])

if __name__=='__main__':unittest.main(argv=[sys.argv[0],*remaining])
