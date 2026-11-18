"""Task drafts and paginated selectors backed by the shared tasks API."""
import secrets
from datetime import datetime

from clients import ApiError


FIELDS = {
    'title': 'Название', 'description': 'Описание', 'deadline': 'Дедлайн',
    'assignee_ids': 'Ответственные', 'department_card_ids': 'Отделы', 'event_card_id': 'Мероприятие',
}
OPTIONS = {'assignee_ids': 'users', 'department_card_ids': 'departments', 'event_card_id': 'events'}
DEFAULTS = {'title': '', 'description': '', 'deadline': None, 'assignee_ids': [],
            'department_card_ids': [], 'event_card_id': None, 'completed': False}


class TaskFormMixin:
    def form_reply(self, state, text, rows):
        self.reply(text, rows, [state['id']] if state['id'] else [])

    def start_task_form(self, task_id, all_tasks):
        state = {'kind': 'task_form', 'flow': secrets.token_hex(8), 'id': task_id,
                 'all_tasks': all_tasks, 'values': dict(DEFAULTS), 'changes': {},
                 'can_change_status': True, 'existing_users': []}
        if task_id is None:
            self.task_prompt(state, 'title')
            return
        task = self.api('GET', f'bot/tasks/{task_id}')
        if task['archived']:
            raise ApiError(409, 'Таска в архиве.')
        state.update(version=task['version'], can_change_status=task['can_change_status'],
                     existing_users=task['assignees'])
        state['values'] = {field: task[field] for field in DEFAULTS}
        self.task_form(state)

    def task_prompt(self, state, field):
        prompts = {'title': 'Введите название таски (до 300 символов).',
                   'description': 'Введите описание. /clear — очистить, /skip — пропустить.',
                   'deadline': 'Введите дедлайн в формате ДД.ММ.ГГГГ. /clear — убрать срок, /skip — пропустить.'}
        state.update(kind='task_input', field=field)
        self.set_state(state)
        self.form_reply(state, prompts[field], [[self.button('Отмена', 'cancel')]])

    def task_form(self, state):
        options = self.api('GET', 'bot/tasks/options')
        values = {**state['values'], **state['changes']}
        lines = ['Редактирование таски' if state['id'] else 'Новая таска',
                 'Название: ' + values['title'][:300],
                 'Описание: ' + (values['description'][:300] or '—'),
                 'Дедлайн: ' + (values['deadline'] or 'без срока')]
        for field in OPTIONS:
            selected = self.task_selection(values, field)
            labels = [option.get('name') or option['title'] for option in self.task_choices(options,state,field)
                      if option['id'] in selected]
            lines.append(FIELDS[field] + ': ' + (', '.join(labels)[:180] or '—'))
        lines.append('Статус: ' + ('выполнена' if values['completed'] else 'в работе'))
        buttons = [self.button(name, 'task_field', flow=state['flow'], field=field)
                   for field,name in FIELDS.items()]
        rows = [buttons[index:index+2] for index in range(0,len(buttons),2)]
        if state['can_change_status']:
            rows.append([self.button('Вернуть в работу' if values['completed'] else 'Отметить выполненной',
                                     'task_form_status', flow=state['flow'], completed=not values['completed'])])
        rows.append([self.button('Сохранить изменения' if state['id'] else 'Создать таску',
                                 'task_save', flow=state['flow']), self.button('Отмена', 'cancel')])
        state['kind'] = 'task_form'
        self.set_state(state)
        self.form_reply(state, '\n'.join(lines), rows)

    @staticmethod
    def task_selection(values, field):
        value = values[field]
        if field == 'event_card_id':
            return [value] if value else []
        return value

    @staticmethod
    def task_choices(options, state, field):
        choices = list(options[OPTIONS[field]])
        if field == 'assignee_ids':
            ids = {row['id'] for row in choices}
            selected = state['changes'].get(field,state['values'][field])
            choices.extend(user for user in state['existing_users'] if user['id'] not in ids and user['id'] in selected)
        return choices

    def task_picker(self, state, field, page=0, query=''):
        options = self.api('GET', 'bot/tasks/options')
        choices = self.task_choices(options,state,field)
        selected = self.task_selection({**state['values'],**state['changes']},field)
        rows = []
        matches = []
        for option in choices:
            name = option.get('name') or option['title']
            if option.get('login'):
                name += ' · ' + option['login']
            if query.casefold() in name.casefold():
                matches.append((option['id'], name))
        page = max(0,min(page,max(0,(len(matches)-1)//5)))
        args = {'flow':state['flow'], 'field':field}
        for option_id,name in matches[page*5:(page+1)*5]:
            rows.append([self.button(('✓ ' if option_id in selected else '')+name, 'task_pick',
                                      **args, id=option_id, page=page, query=query)])
        self.pagination(rows,page,len(matches),'task_pick_page',**args,query=query)
        rows.append([self.button('Поиск', 'task_search', **args), self.button('Очистить выбор', 'task_clear', **args)])
        rows.append([self.button('Готово', 'task_form', flow=state['flow']), self.button('Отмена','cancel')])
        state.update(kind='task_picker',field=field)
        self.set_state(state)
        self.form_reply(state, FIELDS[field]+f' · выбрано {len(selected)}'+('\nНичего не найдено.' if not matches else ''),rows)

    def task_form_action(self, data, token):
        kind = data['kind']
        if kind in {'task_new','task_edit'}:
            self.start_task_form(data.get('id'),data.get('all_tasks',False))
            return True
        if kind not in {'task_field','task_form','task_form_status','task_pick','task_pick_page',
                        'task_search','task_clear','task_save'}:
            return False
        state = self.flow(data)
        field = data.get('field')
        if kind == 'task_save':
            body = dict(state['changes'])
            if state['id']:
                body['version'] = state['version']
                task = self.api('PATCH',f"bot/tasks/{state['id']}",body)
            else:
                task = self.api('POST','bot/tasks',body)
            self.store.done(token)
            self.set_state(None)
            self.notice = 'Изменения сохранены.' if state['id'] else 'Таска создана.'
            self.task(task['id'],all_tasks=state['all_tasks'])
        elif kind == 'task_form_status':
            state['changes']['completed'] = data['completed']
            self.task_form(state)
        elif kind == 'task_form':
            self.task_form(state)
        elif kind == 'task_field' and field not in OPTIONS:
            self.task_prompt(state,field)
        elif kind == 'task_search':
            state.update(kind='task_search_text',field=field)
            self.set_state(state)
            self.form_reply(state,'Введите имя, логин или название для поиска.',
                            [[self.button('К выбору','task_pick_page',field=field,flow=state['flow'])]])
        else:
            if kind == 'task_clear':
                state['changes'][field] = None if field=='event_card_id' else []
            elif kind == 'task_pick':
                options = self.api('GET','bot/tasks/options')
                if data['id'] not in {option['id'] for option in self.task_choices(options,state,field)}:
                    raise ApiError(409,'Выбранная запись больше недоступна.')
                if field == 'event_card_id':
                    state['changes'][field] = data['id']
                    self.task_form(state)
                    return True
                values = set(state['changes'].get(field,state['values'][field]))
                values.symmetric_difference_update([data['id']])
                if len(values)>100:
                    raise ApiError(422,'Можно выбрать не более 100 записей.')
                state['changes'][field] = sorted(values)
            self.task_picker(state,field,data.get('page',0),data.get('query',''))
        return True

    def task_form_text(self, state, text):
        if state['kind']=='task_search_text':
            self.task_picker(state,state['field'],query=text[:200])
            return
        if state['kind']!='task_input':
            self.task_form(state)
            return
        field = state['field']
        if field != 'title' and text=='/skip':
            self.task_form(state)
            return
        if field=='title':
            if len(text)>300 or text in {'/skip','/clear'}:
                raise ApiError(422,'Введите название таски длиной от 1 до 300 символов.')
        elif field=='description':
            if len(text)>20000:
                raise ApiError(422,'Допустимо до 20000 символов.')
            if text=='/clear':
                text=''
        elif text=='/clear':
            text=None
        else:
            try:
                text=datetime.strptime(text,'%d.%m.%Y').date().isoformat()
            except ValueError:
                raise ApiError(422,'Введите действительную дату в формате ДД.ММ.ГГГГ.')
        state['changes'][field]=text
        self.task_form(state)
