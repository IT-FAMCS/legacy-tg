"""Event responsibility zones in chat, sharing the web API and edit rights."""
import secrets
from clients import ApiError

class ZonesMixin:
    def zone_path(self,event_id):return f'bot/events/{event_id}/responsibility-zones'
    def zone_board(self,event_id,write=False):
        board=self.api('GET',self.zone_path(event_id))
        if write and not board['can_edit']:raise ApiError(403,'Нужно право редактирования карточки мероприятия')
        return board
    def zone_find(self,board,zone_id):
        zone=next((z for z in board['zones'] if z['id']==zone_id),None)
        if not zone:raise ApiError(404,'Зона уже удалена. Обновите список.')
        return zone
    def zone_person(self,person):
        if not person:return 'не назначен'
        return person['name'][:150]+(' · '+person['telegram'][:100] if person.get('telegram') else ' · Telegram не указан')+(' (неактивен)' if not person['active'] else '')
    def zone_reply(self,event_id,text,rows):self.reply(text,rows,[f'event:{event_id}'])

    def events(self,page=0):
        events=self.api('GET','bot/events');page=max(0,min(page,max(0,(len(events)-1)//5)));subset=events[page*5:(page+1)*5]
        rows=[[self.button(event['title'],'zone_list',event_id=event['id'])] for event in subset]
        self.pagination(rows,page,len(events),'zone_events');rows.append([self.button('Главное меню','menu')])
        self.reply('Мероприятия · зоны ответственности\nВыберите мероприятие.' if events else 'Нет доступных мероприятий.',rows,[f"event:{e['id']}" for e in subset])

    def zones(self,event_id,page=0):
        board=self.zone_board(event_id);zones=board['zones'];page=max(0,min(page,max(0,(len(zones)-1)//5)))
        rows=[[self.button(('✓ ' if z['closed'] else '○ ')+z['title']+f" · {z['filled']}/{z['people_required']}",'zone_detail',event_id=event_id,id=z['id'])] for z in zones[page*5:(page+1)*5]]
        self.pagination(rows,page,len(zones),'zone_list',event_id=event_id)
        if board['can_edit']:
            rows.append([self.button('Добавить зону','zone_new',event_id=event_id)])
            if any(z['lead'] or z['helpers'] for z in zones):rows.append([self.button('Очистить ответственных','zone_clear_confirm',event_id=event_id)])
        rows.append([self.button('История зон','zone_history',event_id=event_id),self.button('К мероприятиям','zone_events')])
        self.zone_reply(event_id,f"{board['card']['title'][:300]}\nЗон: {board['total']} · закрыто: {board['closed']} · не закрыто: {board['open']}\nГлавный входит в нужное число людей.",rows)

    def zone_detail(self,event_id,zone_id):
        board=self.zone_board(event_id);zone=self.zone_find(board,zone_id)
        helpers='\n'.join(self.zone_person(p) for p in zone['helpers']) or 'Пока никого'
        rows=[]
        if board['can_edit']:
            rows=[[self.button('Назначить главного','zone_lead',event_id=event_id,id=zone_id),self.button('Помогаторы','zone_helpers',event_id=event_id,id=zone_id)],
                  [self.button('Название','zone_field',event_id=event_id,id=zone_id,field='title'),self.button('Нужно людей','zone_field',event_id=event_id,id=zone_id,field='people_required')],
                  [self.button('Удалить зону','zone_delete_confirm',event_id=event_id,id=zone_id)]]
        rows.append([self.button('К зонам мероприятия','zone_list',event_id=event_id)])
        self.zone_reply(event_id,f"{zone['title']}\nЛюди: {zone['filled']} / {zone['people_required']} · {'Закрыта' if zone['closed'] else 'Не закрыта'}\n\nГлавный: {self.zone_person(zone['lead'])}\n\nПомогаторы:\n{helpers}",rows)

    def zone_picker(self,state,page=0,query=''):
        board=self.zone_board(state['event_id'],True);zone=self.zone_find(board,state['zone_id'])
        users=board['users']
        if state.get('picker_role')=='helpers':
            users=users+[p for p in zone['helpers'] if not any(u['id']==p['id'] for u in users)]
            users=[p for p in users if p['id']!=zone['lead_user_id']]
        if query:users=[p for p in users if query.casefold() in (p['name']+' '+p['login']+' '+(p['telegram'] or '')).casefold()]
        page=max(0,min(page,max(0,(len(users)-1)//5)));rows=[]
        for user in users[page*5:(page+1)*5]:
            prefix='✓ ' if user['id'] in state.get('helper_ids',[]) else ''
            rows.append([self.button(prefix+self.zone_person(user),'zone_pick',flow=state['flow'],id=user['id'])])
        self.pagination(rows,page,len(users),'zone_pick_page',flow=state['flow'],query=query)
        if state.get('picker_role')=='lead':rows.append([self.button('Снять главного','zone_pick',flow=state['flow'],id=None)])
        else:rows.append([self.button('Сохранить помогаторов','zone_save_helpers',flow=state['flow'])])
        rows.append([self.button('Искать человека','zone_search',flow=state['flow']),self.button('Отмена','cancel')])
        self.zone_reply(state['event_id'],'Выберите главного' if state.get('picker_role')=='lead' else f"Помогаторы · выбрано {len(state['helper_ids'])}",rows)

    def zone_action(self,data,token):
        kind=data['kind']
        if not kind.startswith('zone_'):return False
        event_id=data.get('event_id');zone_id=data.get('id');page=data.get('page',0)
        if kind=='zone_events':self.events(page)
        elif kind=='zone_list':self.zones(event_id,page)
        elif kind=='zone_detail':self.zone_detail(event_id,zone_id)
        elif kind=='zone_history':
            rows=self.api('GET',self.zone_path(event_id)+'/history');subset=rows[page*5:(page+1)*5]
            text='История зон\n\n'+'\n\n'.join(f"{r['action']} · {(r['after'] or r['before'])['title']}\n{r['actor_name']} · {r['created_at']}\nБыло: {self.zone_person((r['before'] or {}).get('lead'))}\nСтало: {self.zone_person((r['after'] or {}).get('lead'))}" for r in subset)
            buttons=[];self.pagination(buttons,page,len(rows),'zone_history',event_id=event_id);buttons.append([self.button('К зонам мероприятия','zone_list',event_id=event_id)])
            self.zone_reply(event_id,text,buttons)
        elif kind in {'zone_new','zone_field','zone_lead','zone_helpers'}:
            board=self.zone_board(event_id,True);flow=secrets.token_hex(8)
            if kind=='zone_new':
                self.set_state({'kind':'zone_create_title','event_id':event_id,'board_version':board['version'],'flow':flow,'payload':{}})
                self.zone_reply(event_id,'Введите название зоны (до 200 символов). /cancel — отмена.',[])
            else:
                zone=self.zone_find(board,zone_id)
                state={'kind':kind,'event_id':event_id,'zone_id':zone_id,'flow':flow,'board_version':board['version'],'version':zone['version'],'helper_ids':zone['helper_ids'][:],'payload':{}}
                if kind=='zone_field':state.update(kind='zone_edit_text',field=data['field']);self.set_state(state);self.zone_reply(event_id,'Введите новое название зоны.' if data['field']=='title' else 'Сколько людей нужно, включая главного? (1–1000)',[])
                else:state['picker_role']='lead' if kind=='zone_lead' else 'helpers';self.set_state(state);self.zone_picker(state)
        elif kind in {'zone_pick','zone_save_helpers','zone_pick_page','zone_search','zone_save'}:
            state=self.flow(data);event_id=state['event_id'];board=self.zone_board(event_id,True)
            if kind=='zone_pick_page':self.zone_picker(state,page,data.get('query',''))
            elif kind=='zone_search':state['kind']='zone_search_text';self.set_state(state);self.zone_reply(event_id,'Введите имя, логин или Telegram.',[])
            elif kind=='zone_pick':
                uid=data.get('id')
                if uid is not None and not any(p['id']==uid for p in board['users']):raise ApiError(409,'Пользователь больше недоступен')
                zone=self.zone_find(board,state['zone_id'])
                if state.get('picker_role')=='helpers':
                    state['kind']='zone_helpers'
                    if uid==zone['lead_user_id']:raise ApiError(422,'Главный уже учитывается в составе')
                    state['helper_ids']=([x for x in state['helper_ids'] if x!=uid] if uid in state['helper_ids'] else state['helper_ids']+[uid]);self.set_state(state);self.zone_picker(state)
                elif state.get('picker_role')=='lead':
                    self.api('PATCH',self.zone_path(event_id)+f"/{zone['id']}",{'version':state['version'],'board_version':state['board_version'],'lead_user_id':uid,'helper_ids':[x for x in zone['helper_ids'] if x!=uid]})
                    self.store.done(token);self.set_state(None);self.zone_detail(event_id,zone['id'])
            elif kind=='zone_save_helpers':
                if state.get('picker_role')!='helpers':raise ApiError(409,'Выбор помогаторов уже завершён')
                self.api('PATCH',self.zone_path(event_id)+f"/{state['zone_id']}",{'version':state['version'],'board_version':state['board_version'],'helper_ids':state['helper_ids']})
                self.store.done(token);self.set_state(None);self.zone_detail(event_id,state['zone_id'])
            else:
                payload={**state['payload'],'board_version':state['board_version']}
                creating='zone_id' not in state
                if not creating:payload['version']=state['version']
                result=self.api('POST' if creating else 'PATCH',self.zone_path(event_id)+('' if creating else '/'+str(state['zone_id'])),payload)
                self.store.done(token);self.set_state(None)
                self.zone_detail(event_id,result['zones'][-1]['id'] if creating else state['zone_id'])
        elif kind in {'zone_clear_confirm','zone_delete_confirm'}:
            board=self.zone_board(event_id,True)
            if kind=='zone_clear_confirm':
                self.zone_reply(event_id,'Очистить главных и помогаторов во всех зонах этого мероприятия? Зоны и нужное число людей сохранятся.',[[self.button('Очистить все назначения','zone_clear',event_id=event_id,board_version=board['version'])],[self.button('Отмена','zone_list',event_id=event_id)]])
            else:
                zone=self.zone_find(board,zone_id)
                self.zone_reply(event_id,f"Удалить зону «{zone['title']}»?",[[self.button('Удалить','zone_delete',event_id=event_id,id=zone_id,version=zone['version'],board_version=board['version'])],[self.button('Отмена','zone_detail',event_id=event_id,id=zone_id)]])
        elif kind in {'zone_clear','zone_delete'}:
            self.zone_board(event_id,True)
            path=self.zone_path(event_id)+('/clear' if kind=='zone_clear' else '/'+str(zone_id))
            payload={'board_version':data['board_version']}
            if kind=='zone_delete':payload['version']=data['version']
            self.api('POST' if kind=='zone_clear' else 'DELETE',path,payload);self.store.done(token);self.set_state(None);self.zones(event_id)
        else:raise ApiError(409,'Кнопка зоны устарела')
        return True

    def zone_text(self,state,text):
        event_id=state['event_id'];self.zone_board(event_id,True);kind=state['kind']
        if kind in {'zone_create_title','zone_edit_text'}:
            field=state.get('field','title')
            if field=='title':
                if not 1<=len(text)<=200:raise ApiError(422,'Название: 1–200 символов')
                state['payload']['title']=text
            else:
                if not text.isdecimal() or not 1<=int(text)<=1000:raise ApiError(422,'Нужно целое число от 1 до 1000')
                state['payload']['people_required']=int(text)
            if kind=='zone_create_title':state['kind']='zone_create_count';self.set_state(state);self.zone_reply(event_id,'Сколько людей нужно, включая главного? (1–1000)',[]);return
        elif kind=='zone_create_count':
            if not text.isdecimal() or not 1<=int(text)<=1000:raise ApiError(422,'Нужно целое число от 1 до 1000')
            state['payload']['people_required']=int(text)
        elif kind=='zone_search_text':
            state['kind']='zone_lead' if state.get('picker_role')=='lead' else 'zone_helpers';self.set_state(state);self.zone_picker(state,query=text[:200]);return
        else:self.zone_reply(event_id,'Используйте кнопки для назначения или /cancel.',[]);return
        state['kind']='zone_confirm';self.set_state(state)
        self.zone_reply(event_id,'Проверьте изменения:\n'+'\n'.join(f'{key}: {value}' for key,value in state['payload'].items()),[[self.button('Создать зону' if 'zone_id' not in state else 'Сохранить зону','zone_save',flow=state['flow'])],[self.button('Отмена','cancel')]])
