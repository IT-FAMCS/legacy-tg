"""One polling consumer and one independent reply/notification delivery worker."""
import logging
import sqlite3
import threading
import time
from chat import Chat
from clients import ApiError, TelegramError, TransportError
from worker import DeliveryWorker

LOG=logging.getLogger(__name__)
COMMANDS=[{'command':name,'description':description} for name,description in [
    ('start','Главное меню'),('tasks','Мои действующие таски'),('equipment','Оборудка'),
    ('zones','Зоны мероприятий'),('link','Привязка аккаунта'),('unlink','Отключить аккаунт'),('cancel','Отменить ввод'),('help','Помощь')]]

class Runtime:
    def __init__(self,backend,telegram,store,webapp_url,notification_interval=10):
        self.backend,self.telegram,self.store=backend,telegram,store
        self.chat=Chat(backend,store,webapp_url)
        self.worker=DeliveryWorker(backend,telegram,store)
        self.notification_interval=max(2,int(notification_interval))
        self.stop_event=threading.Event();self.threads=[];self.last_poll=0;self.failed=False

    def check_startup(self):
        info=self.telegram.call('getWebhookInfo',{})
        if not isinstance(info,dict) or info.get('url'):
            raise RuntimeError('An existing webhook is configured. Remove it explicitly before enabling polling.')
        self.telegram.call('setMyCommands',{'commands':COMMANDS})

    def ready(self):
        clock=time.monotonic()
        return not self.failed and clock-self.last_poll<90 and clock-self.worker.last_backend_ok<120

    def start(self):
        self.check_startup()
        for target in [self.poll,self.deliver]:
            thread=threading.Thread(target=target,daemon=True);thread.start();self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:thread.join(timeout=2)
        self.store.close()

    def poll(self):
        delay=1
        while not self.stop_event.is_set():
            try:
                updates=self.telegram.call('getUpdates',{'offset':self.store.offset(),'timeout':25,'limit':30,
                    'allowed_updates':['message','callback_query']})
                if not isinstance(updates,list):raise TransportError('Unexpected polling response')
                self.last_poll=time.monotonic()
                for update in updates:
                    if self.stop_event.is_set():break
                    self.chat.process(update)
                delay=1
            except TelegramError as error:
                if error.code==429:self.store.pause_sending(error.retry_after or 30)
                if error.code in {401,409}:
                    LOG.error('Polling stopped: invalid token or another update consumer');self.failed=True;self.stop_event.set();break
                self.stop_event.wait(min(error.retry_after or delay,30));delay=min(30,delay*2)
            except (ApiError,TransportError,OSError,sqlite3.Error,ValueError,KeyError,TypeError):
                LOG.warning('Polling or chat processing temporarily unavailable; retrying')
                self.stop_event.wait(delay);delay=min(30,delay*2)

    def deliver(self):
        next_notifications=0
        while not self.stop_event.is_set():
            try:
                self.worker.flush_replies()
                if time.monotonic()>=next_notifications:
                    self.worker.notifications();next_notifications=time.monotonic()+self.notification_interval
            except (ApiError,TelegramError,TransportError,OSError,sqlite3.Error,ValueError,KeyError,TypeError):
                LOG.warning('Delivery temporarily unavailable; queued items will retry')
                next_notifications=time.monotonic()+self.notification_interval
            self.stop_event.wait(1)
