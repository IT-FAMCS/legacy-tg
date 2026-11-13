"""Private chat controller: durable callbacks and cancellable equipment wizards."""
import secrets
from datetime import datetime, timedelta, timezone
from clients import ApiError
from zones_chat import ZonesMixin

PAGE=5


def clip(value,units=200):
    data=str(value or '').encode('utf-16-le')
    return data[:units*2].decode('utf-16-le',errors='ignore') + ('…' if len(data)>units*2 else '')


def chunks(value,units=1400):
    data=str(value or '').encode('utf-16-le');result=[]
    while data:
        piece=data[:units*2]
        # Do not split a UTF-16 surrogate pair.
        if len(piece)>=2 and 0xD800<=int.from_bytes(piece[-2:],'little')<=0xDBFF:piece=piece[:-2]
        result.append(piece.decode('utf-16-le'));data=data[len(piece):]
    return result or ['—']


def date_label(value):
    if not value:return 'Не взят'
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M')
    except ValueError:return str(value)


class Chat(ZonesMixin):
    def __init__(self,backend,store,webapp_url):
        self.backend,self.store,self.webapp_url=backend,store,webapp_url.rstrip('/')
        self.me=None

    def api(self,method,path,body=None):
        return self.backend.request(method,path,body,tid=self.tid,request_id=self.request_id if method!='GET' else None,generation=self.generation or None)

    @property
    def generation(self):return self.me['generation'] if self.me else ''

    def button(self,text,kind,**values):
        return {'text':clip(text,55),'callback_data':self.store.callback(self.tid,self.generation,{'kind':kind,**values})}

    def reply(self,text,rows=None,task_ids=()):
        parts=chunks(text,3500)
        for index,part in enumerate(parts):
            payload={'chat_id':self.tid,'text':part,'link_preview_options':{'is_disabled':True}}
            if rows and index==len(parts)-1:payload['reply_markup']={'inline_keyboard':rows}
            self.store.enqueue('sendMessage',payload,self.tid,self.generation or None,task_ids)

    def main_menu(self,text='Наследие · выберите раздел'):
        self.reply(text,[[self.button('Мои таски','tasks')],[self.button('Оборудка','equipment')],[self.button('Мероприятия · зоны','zone_events')],
                         [{'text':'Открыть Наследие','url':self.webapp_url}],
                         [self.button('Привязка аккаунта','link')]])

    def link_prompt(self):
        self.reply('Привязать аккаунт: откройте свой профиль Наследия, войдите обычным способом и создайте ссылку Telegram. После открытия бота подтвердите привязку на сайте. Пароль в этот чат отправлять не нужно.',
                   [[{'text':'Привязать аккаунт','url':self.webapp_url+'/account'}]])

    def process(self,update):
        update_id=update.get('update_id')
        if type(update_id) is not int:return
        with self.store.update():
            if self.store.processed(update_id):return
            message=update.get('message') or {}
            callback=update.get('callback_query') or {}
            sender=callback.get('from') or message.get('from') or {}
            chat=(callback.get('message') or message).get('chat') or {}
            tid=sender.get('id')
            if type(tid) is not int or tid<=0 or chat.get('type')!='private' or chat.get('id')!=tid or sender.get('is_bot'):
                self.store.finish(update_id);return
            self.tid=tid;self.me=None;self.request_id=f'update:{update_id}'
            text=message.get('text') or ''
            command=text.split(maxsplit=1)[0].split('@')[0] if text else ''
            try:
                if command=='/start' and len(text.split(maxsplit=1))==2 and text.split(maxsplit=1)[1].startswith('link_'):
                    if message.get('forward_origin') or message.get('forward_from'):
                        self.reply('Откройте личную ссылку из своего профиля, не пересылайте её.');self.store.finish(update_id);return
                    token=text.split(maxsplit=1)[1][5:]
                    self.api('POST','bot/claim',{'token':token,'telegram_user_id':tid,'chat_id':tid,
                        'username':sender.get('username'),'display_name':' '.join(filter(None,[sender.get('first_name'),sender.get('last_name')]))})
                    self.reply('Telegram найден. Вернитесь в профиль Наследия и подтвердите привязку. Затем нажмите /start.');self.store.finish(update_id);return
                try:self.me=self.api('GET','bot/me?contact=true')
                except ApiError as error:
                    if error.status!=403:raise
                    self.store.set_state(tid,'',None);self.link_prompt();self.store.finish(update_id);return
                if callback:
                    self.store.enqueue('answerCallbackQuery',{'callback_query_id':callback['id']})
                    token=callback.get('data','')
                    entry=self.store.action(token,tid,self.generation)
                    if not entry:self.reply('Кнопка устарела или принадлежит другому аккаунту. Откройте /tasks или /equipment.')
                    elif entry['done']:self.reply('Это действие уже выполнено. Откройте актуальный список.');
                    else:
                        self.request_id='callback:'+token
                        self.action(entry['data'],token)
                elif command=='/cancel':
                    self.store.set_state(tid,self.generation,None);self.main_menu('Изменения отменены.')
                elif command in {'/start','/help','/tasks','/equipment','/zones','/link','/unlink'}:
                    self.store.set_state(tid,self.generation,None)
                    if command=='/tasks':self.tasks()
                    elif command=='/equipment':self.equipment()
                    elif command=='/zones':self.events()
                    elif command=='/link':self.reply(f"Аккаунт привязан: {self.me['name']}. Отключить: /unlink.",[[{'text':'Профиль','url':self.webapp_url+'/account'}]])
                    elif command=='/unlink':self.reply('Отключить Telegram от аккаунта Наследия?',[[self.button('Отключить','unlink')],[self.button('Отмена','menu')]])
                    else:self.main_menu('Наследие · таски и оборудка в чате.\n/tasks — свои таски\n/equipment — оборудка\n/zones — зоны мероприятий\n/cancel — отменить ввод\n/unlink — отключить аккаунт')
                else:self.text(text)
            except ApiError as error:
                self.reply(str(error),[[self.button('Главное меню','menu')]])
                if error.status in {403,404,409,410}:self.store.set_state(tid,self.generation,None)
            self.store.finish(update_id)

    def tasks(self,page=0):
        tasks=self.api('GET','bot/tasks');page=max(0,min(page,max(0,(len(tasks)-1)//PAGE)))
        subset=tasks[page*PAGE:(page+1)*PAGE]
        rows=[[self.button(task['title'],'task',id=task['id'])] for task in subset]
        self.pagination(rows,page,len(tasks),'tasks')
        rows.append([self.button('Главное меню','menu')])
        self.reply(f'Мои действующие таски · {len(tasks)}\nВыберите таску.' if tasks else 'Действующих тасок нет.',rows,[task['id'] for task in subset])

    def pagination(self,rows,page,total,kind,**values):
        navigation=[]
        if page>0:navigation.append(self.button('← Назад',kind,page=page-1,**values))
        if (page+1)*PAGE<total:navigation.append(self.button('Далее →',kind,page=page+1,**values))
        if navigation:rows.append(navigation)

    def task(self,task_id,page=0):
        task=self.api('GET',f'bot/tasks/{task_id}')
        pieces=chunks(task.get('description') or 'Без описания',1400);page=max(0,min(page,len(pieces)-1))
        text=f"{clip(task['title'],500)}\nДедлайн: {task.get('deadline') or 'без срока'}\n"
        text+='Отделы: '+clip(', '.join(d['title'] for d in task.get('departments',[])) or '—',300)+'\n'
        text+='Мероприятие: '+clip((task.get('event') or {}).get('title','—'),200)+'\n\n'+pieces[page]
        rows=[]
        if not task.get('completed'):rows.append([self.button('Выполнено','complete',id=task_id,version=task['version'])])
        if page>0:rows.append([self.button('← Описание','task',id=task_id,page=page-1)])
        if page+1<len(pieces):rows.append([self.button('Описание далее →','task',id=task_id,page=page+1)])
        rows.append([self.button('К моим таскам','tasks')])
        self.reply(text,rows,[task_id])

    def equipment(self,page=0,query=''):
        items=self.api('GET','bot/equipment');options=self.api('GET','bot/equipment/options')
        if query:items=[i for i in items if query.casefold() in (i['name']+' '+i.get('description','')).casefold()]
        page=max(0,min(page,max(0,(len(items)-1)//PAGE)))
        rows=[[self.button(item['name'],'item',id=item['id'])] for item in items[page*PAGE:(page+1)*PAGE]]
        self.pagination(rows,page,len(items),'equipment',query=query)
        rows.append([self.button('Поиск','search')])
        if options['can_manage']:rows.append([self.button('Добавить предмет','create')])
        rows.append([self.button('Главное меню','menu')])
        self.reply(f'Оборудка · {len(items)}\n'+(f'Поиск: {clip(query,100)}' if query else 'Выберите предмет, чтобы посмотреть детали и действия.'),rows)

    def get_item(self,item_id):
        item=next((i for i in self.api('GET','bot/equipment') if i['id']==item_id),None)
        if not item:raise ApiError(404,'Предмет уже удалён. Обновите /equipment.')
        return item

    def item(self,item_id,page=0):
        item=self.get_item(item_id);options=self.api('GET','bot/equipment/options')
        holder=item.get('holder_user') or item.get('location') or {}
        pieces=chunks(item.get('description') or 'Без описания',1400);page=max(0,min(page,len(pieces)-1))
        text=f"{clip(item['name'],400)}\nУ кого / где: {clip(holder.get('name','—'),300)}\nВзят: {date_label(item.get('taken_at'))}\n\n{pieces[page]}"
        rows=[[self.button('Взял предмет','take',id=item_id,version=item['version']),self.button('История','history',id=item_id)]]
        if options['can_manage']:
            rows.append([self.button('Редактировать','edit',id=item_id),self.button('Передать / место','transfer',id=item_id)])
        if options['can_delete']:rows.append([self.button('Удалить предмет','delete_confirm',id=item_id)])
        if page>0:rows.append([self.button('← Описание','item',id=item_id,page=page-1)])
        if page+1<len(pieces):rows.append([self.button('Описание далее →','item',id=item_id,page=page+1)])
        rows.append([self.button('К списку оборудки','equipment')]);self.reply(text,rows)

    def history(self,item_id,page=0):
        entries=self.api('GET',f'bot/equipment/{item_id}/history');page=max(0,min(page,max(0,(len(entries)-1)//PAGE)))
        labels={'created':'Добавлен','taken':'Взят','assigned':'Передан','deleted':'Удалён'}
        text='История предмета\n\n'+'\n\n'.join(f"{labels.get(e['action'],e['action'])} · {date_label(e.get('recorded_at'))}\nКто: {clip(e['actor_name'],150)}\nУ кого / где: {clip(e['holder_name'],150)}\nВзят: {date_label(e.get('taken_at'))}" for e in entries[page*PAGE:(page+1)*PAGE])
        rows=[];self.pagination(rows,page,len(entries),'history',id=item_id)
        rows.append([self.button('К списку оборудки','equipment')]);self.reply(text if entries else 'История пуста.',rows)

    def state(self):return self.store.state(self.tid,self.generation)
    def set_state(self,state):self.store.set_state(self.tid,self.generation,state)
    def flow(self,action):
        state=self.state()
        if not state or state.get('flow')!=action.get('flow'):raise ApiError(409,'Ввод отменён или истёк. Начните действие заново.')
        return state
    def options_manager(self):
        options=self.api('GET','bot/equipment/options')
        if not options['can_manage']:raise ApiError(403,'Редактирование доступно только руководству')
        return options

    def draft(self,state):
        state['kind']='draft';self.set_state(state)
        payload=state['payload'];mode=state['mode'];flow=state['flow']
        text=('Новый предмет' if mode=='create' else 'Проверьте изменения')+'\n'
        text+='Название: '+clip(payload.get('name',state.get('name','—')),300)+'\n'
        if 'description' in payload:text+='Описание: '+clip(payload['description'] or '—',500)+'\n'
        options=self.options_manager()
        rows=[]
        if mode in {'create','transfer'}:
            uid=payload.get('holder_user_id');lid=payload.get('location_id')
            holder=next((u['name'] for u in options['users'] if u['id']==uid),None) if uid else next((p['name'] for p in options['locations'] if p['id']==lid),'—')
            text+='У кого / где: '+clip(holder or '—',250)+'\n'
            rows.append([self.button('Выбрать пользователя','pick_holder',flow=flow,scope='users'),self.button('Место хранения','pick_holder',flow=flow,scope='locations')])
            rows.append([self.button('Добавить место','new_location',flow=flow)])
            if uid:rows.append([self.button('Дата взятия','draft_date',flow=flow)])
        if payload.get('taken_at'):text+='Дата: '+date_label(payload['taken_at'])+'\n'
        rows.append([self.button('Создать' if mode=='create' else 'Сохранить','save_draft',flow=flow)])
        rows.append([self.button('Отмена','cancel')]);self.reply(text,rows)

    def picker(self,state,scope,page=0,query=''):
        options=self.options_manager();items=options[scope]
        if query:items=[x for x in items if query.casefold() in (x['name']+' '+x.get('login','')).casefold()]
        page=max(0,min(page,max(0,(len(items)-1)//PAGE)));flow=state['flow']
        rows=[[self.button(x['name']+(' · '+x['login'] if x.get('login') else ''),'holder',flow=flow,scope=scope,id=x['id'])] for x in items[page*PAGE:(page+1)*PAGE]]
        self.pagination(rows,page,len(items),'pick_holder',flow=flow,scope=scope,query=query)
        rows.append([self.button('Искать','holder_search',flow=flow,scope=scope),self.button('К изменениям','draft',flow=flow)])
        self.reply('Выберите пользователя' if scope=='users' else 'Выберите место хранения',rows)

    def action(self,data,token):
        if self.zone_action(data,token):return
        kind=data['kind'];page=data.get('page',0);item_id=data.get('id')
        if kind=='menu':self.main_menu()
        elif kind=='link':self.reply('Управление привязкой: /link или /unlink.',[[{'text':'Профиль','url':self.webapp_url+'/account'}]])
        elif kind=='tasks':self.tasks(page)
        elif kind=='task':self.task(item_id,page)
        elif kind=='complete':
            self.api('PATCH',f'bot/tasks/{item_id}',{'version':data['version'],'completed':True})
            self.store.done(token);self.reply('Таска выполнена.');self.tasks()
        elif kind=='equipment':self.equipment(page,data.get('query',''))
        elif kind=='item':self.item(item_id,page)
        elif kind=='history':self.history(item_id,page)
        elif kind=='take':
            self.api('POST',f'bot/equipment/{item_id}/take',{'version':data['version']})
            self.store.done(token);self.reply('Предмет записан на вас. Время выставлено автоматически.');self.item(item_id)
        elif kind=='search':self.set_state({'kind':'equipment_search'});self.reply('Введите название или описание для поиска. /cancel — отмена.')
        elif kind=='create':
            self.options_manager();self.set_state({'kind':'create_name','mode':'create','flow':secrets.token_hex(8),'payload':{}})
            self.reply('Название нового предмета (до 200 символов). /cancel — отмена.')
        elif kind=='edit':
            self.options_manager();item=self.get_item(item_id)
            rows=[[self.button('Название','edit_field',id=item_id,field='name'),self.button('Описание','edit_field',id=item_id,field='description')]]
            if item.get('holder_user'):rows.append([self.button('Дата взятия','edit_field',id=item_id,field='taken_at')])
            rows.append([self.button('Назад к предмету','item',id=item_id)]);self.reply('Что изменить?',rows)
        elif kind=='edit_field':
            self.options_manager();item=self.get_item(item_id);field=data['field']
            self.set_state({'kind':'manual_date' if field=='taken_at' else 'edit_text','field':field,'mode':'edit','id':item_id,'name':item['name'],
                'flow':secrets.token_hex(8),'payload':{'version':item['version']}})
            self.reply('Введите дату ДД.ММ.ГГГГ ЧЧ:ММ (Москва).' if field=='taken_at' else 'Введите новое '+('название (до 200 символов).' if field=='name' else 'описание (до 5000 символов). /clear — очистить.'))
        elif kind=='transfer':
            self.options_manager();item=self.get_item(item_id)
            state={'kind':'draft','mode':'transfer','id':item_id,'name':item['name'],'flow':secrets.token_hex(8),
                   'payload':{'version':item['version'],'holder_user_id':(item.get('holder_user') or {}).get('id'),'location_id':(item.get('location') or {}).get('id')}}
            self.set_state(state);self.picker(state,'users')
        elif kind in {'draft','pick_holder','holder_search','holder','new_location','draft_date','save_draft'}:
            state=self.flow(data)
            if kind=='draft':self.draft(state)
            elif kind=='pick_holder':self.picker(state,data['scope'],page,data.get('query',''))
            elif kind=='holder_search':state.update(kind='holder_search',scope=data['scope']);self.set_state(state);self.reply('Введите имя, логин или название места.')
            elif kind=='holder':
                options=self.options_manager()
                if not any(x['id']==item_id for x in options[data['scope']]):raise ApiError(409,'Этот вариант больше недоступен')
                state['payload'].update(holder_user_id=item_id if data['scope']=='users' else None,location_id=item_id if data['scope']=='locations' else None)
                state['payload'].pop('taken_at',None);self.draft(state)
            elif kind=='new_location':self.options_manager();state['kind']='new_location';self.set_state(state);self.reply('Название нового места хранения (до 120 символов).')
            elif kind=='draft_date':state['kind']='manual_date';self.set_state(state);self.reply('Введите дату ДД.ММ.ГГГГ ЧЧ:ММ (Москва) или /skip для автоматической даты.')
            else:
                self.options_manager()
                path='bot/equipment'+('' if state['mode']=='create' else '/'+str(state['id']))
                result=self.api('POST' if state['mode']=='create' else 'PATCH',path,state['payload'])
                self.store.done(token);self.set_state(None);self.reply('Предмет сохранён.');self.item(result['id'])
        elif kind=='delete_confirm':
            options=self.api('GET','bot/equipment/options')
            if not options['can_delete']:raise ApiError(403,'Удаление недоступно для вашей роли')
            item=self.get_item(item_id)
            self.reply(f"Удалить «{clip(item['name'],300)}» из списка? История сохранится.",[[self.button('Удалить','delete',id=item_id,version=item['version'])],[self.button('Отмена','item',id=item_id)]])
        elif kind=='delete':
            self.api('DELETE',f'bot/equipment/{item_id}',{'version':data['version']});self.store.done(token);self.reply('Предмет удалён. История сохранена.');self.equipment()
        elif kind=='unlink':
            self.api('POST','bot/unlink',{});self.store.done(token);self.set_state(None);self.me=None;self.reply('Telegram отключён от Наследия.');self.link_prompt()
        elif kind=='cancel':self.set_state(None);self.main_menu('Изменения отменены.')
        else:self.reply('Кнопка устарела. Начните с /start.')

    def text(self,text):
        state=self.state()
        if not state:self.main_menu('Выберите действие кнопками или используйте /tasks и /equipment.');return
        text=text.strip()
        if not text:self.reply('Нужен текст. /cancel — отменить ввод.');return
        kind=state['kind']
        if kind.startswith('zone_'):self.zone_text(state,text);return
        if kind=='equipment_search':self.set_state(None);self.equipment(query=text[:200]);return
        self.options_manager()
        if kind=='create_name':
            if len(text)>200:raise ApiError(422,'Название должно быть не длиннее 200 символов')
            state['payload']['name']=text;state['kind']='create_description';self.set_state(state);self.reply('Введите описание (до 5000 символов) или /skip.')
        elif kind=='create_description':
            if len(text)>5000:raise ApiError(422,'Описание должно быть не длиннее 5000 символов')
            state['payload']['description']='' if text=='/skip' else text
            state['payload']['location_id']=self.options_manager()['default_location_id'];self.draft(state)
        elif kind=='edit_text':
            field=state['field'];limit=200 if field=='name' else 5000
            if len(text)>limit:raise ApiError(422,f'Допустимо до {limit} символов')
            state['payload'][field]='' if field=='description' and text=='/clear' else text;self.draft(state)
        elif kind=='new_location':
            if len(text)>120:raise ApiError(422,'Допустимо до 120 символов')
            try:location=self.api('POST','bot/equipment/locations',{'name':text})
            except ApiError as error:
                if error.status!=409:raise
                location=next((p for p in self.options_manager()['locations'] if p['name'].casefold()==text.casefold()),None)
                if not location:raise
            state['payload'].update(holder_user_id=None,location_id=location['id']);state['payload'].pop('taken_at',None);self.draft(state)
        elif kind=='holder_search':state['kind']='draft';self.set_state(state);self.picker(state,state['scope'],query=text[:200])
        elif kind=='manual_date':
            if text=='/skip':state['payload'].pop('taken_at',None)
            else:
                try:stamp=datetime.strptime(text,'%d.%m.%Y %H:%M')
                except ValueError:raise ApiError(422,'Формат даты: ДД.ММ.ГГГГ ЧЧ:ММ, например 20.11.2026 15:30')
                state['payload']['taken_at']=stamp.replace(tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc).isoformat()
            self.draft(state)
        else:self.reply('Подтвердите изменения кнопкой или используйте /cancel.')
