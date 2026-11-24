"""Owner-only payout templates and explicit paginated personal-data consents."""
import secrets
import re
from html import escape
from clients import ApiError

BANK_HINT='Белинвест: номер карты, остальные банки - ерип или номер счета'
PAYOUT_WARNING='<b><u>Не указывайте CVV/CVC, PIN, коды подтверждения и пароли.</u></b>'


def policy_emphasis(text):
    """Escape policy configuration before adding Telegram HTML entities."""
    parts=re.split(r'(\n\n|(?<=[.!?]) +)',text)
    result=[]
    for part in parts:
        if re.search(r'добровольн|не отправляйте|отозвать|удален|удалён|удалени|отказ|за пределами|риски|вправе',part,re.I):
            result.append('<b><u>'+escape(part)+'</u></b>')
        elif ': ' in part:
            label,body=part.split(': ',1)
            result.append('<b><u>'+escape(label)+':</u></b> '+escape(body))
        else:result.append(escape(part))
    return ''.join(result)

class SavedPayoutMixin:
    def budget_privacy(self,scope='service',next_action='menu',page=0,flow=None,draft=None):
        status=self.api('GET','bot/personal-data/me');policy=status['policy']
        if not policy['configured']:
            self.set_state(None)
            self.reply('Условия обработки данных ещё не заполнены оператором. Обратитесь к администратору.',[[self.button('Главное меню','menu')]])
            return
        identity=[]
        if policy.get('operator'):identity.append('Оператор: '+policy['operator'])
        if policy.get('address'):identity.append('Адрес: '+policy['address'])
        identity.append('Контакт: '+policy['contact'])
        paragraphs=['\n'.join(identity),
            *policy['sections'],f"Место обработки и уполномоченные лица: {policy['processors']}",
            f"Срок согласия и хранения: {policy['retention']}",'Ваши права и последствия согласия:',*policy['rights']]
        pages=[];part=''
        for paragraph in paragraphs:
            # Configured paragraphs may be long; bound Telegram UTF-16 message size.
            for offset in range(0,len(paragraph),1400):
                fragment=paragraph[offset:offset+1400]
                if len((part+'\n\n'+fragment).encode('utf-16-le'))>5400:
                    pages.append(part);part=''
                part+=(('\n\n' if part else '')+fragment)
        if part:pages.append(part)
        page=max(0,min(page,len(pages)-1));flow=flow or secrets.token_hex(8)
        self.set_state({'kind':'budget_privacy','step':'consent','scope':scope,'next':next_action,'page':page,'last':len(pages)-1,'version':policy['version'],'flow':flow,'draft':draft})
        rows=[]
        if page:rows.append([self.button('Назад','budget_privacy_page',page=page-1,flow=flow)])
        if page<len(pages)-1:rows.append([self.button('Далее','budget_privacy_page',page=page+1,flow=flow)])
        else:rows.append([self.button('Согласен и продолжить','budget_privacy_accept',flow=flow)])
        if policy['contact'].startswith('https://'):
            rows.append([{'text':'Контакт для обращений','url':policy['contact']}])
        rows.append([self.button('Отказаться','cancel')])
        purpose='Добровольное хранение до 3 реквизитов для повторного использования в заявках' if scope=='saved_details' else 'Обработка данных для работы в Наследии'
        self.reply('<b><u>'+escape(purpose)+f'</u></b>\nУсловия {page+1}/{len(pages)}\n\n'+policy_emphasis(pages[page]),rows,parse_mode='HTML')

    def budget_saved_menu(self):
        self.set_state(None)
        rows=self.api('GET','bot/payout-details')
        text=f'Сохранённые реквизиты ({len(rows)}/3). '+BANK_HINT+'\nНовые реквизиты заявки автоматически не сохраняются.'
        buttons=[]
        for row in rows:
            text+=f"\n{row['label']}: …{row['details'][-4:]}"
            buttons.append([self.button('Удалить: '+row['label'],'budget_saved_delete',id=row['id'])])
        if len(rows)<3:buttons.append([self.button('Добавить реквизиты','budget_saved_add')])
        buttons.extend([[self.button('Условия и отзыв согласия','budget_privacy_controls')],[self.button('Назад в бюджет','budget_menu')]])
        self.reply(escape(text)+'\n\n'+PAYOUT_WARNING,buttons,parse_mode='HTML')

    def budget_saved_add(self):
        status=self.api('GET','bot/personal-data/me')
        if not status['service']:self.budget_privacy('service','saved_add');return
        if not status['saved_details']:self.budget_privacy('saved_details','saved_add');return
        if len(self.api('GET','bot/payout-details'))>=3:self.budget_saved_menu();return
        self.set_state({'kind':'budget_saved_form','step':'label','flow':secrets.token_hex(8),'values':{}})
        self.reply('Введите название реквизитов (до 60 символов), например «Белинвест».',[[self.button('Отмена','cancel')]])

    def budget_saved_message(self,state,text):
        if state['kind']!='budget_saved_form':return False
        step=state['step']
        if step=='review':self.reply('Подтвердите сохранение кнопкой или /cancel.');return True
        limit=60 if step=='label' else 500
        if not text or len(text)>limit:self.reply(f'Введите текст до {limit} символов.');return True
        state['values'][step]=text
        if step=='label':
            state['step']='details';self.set_state(state)
            self.reply('Введите реквизиты. '+BANK_HINT+'. До 500 символов. '+PAYOUT_WARNING+' Не присылайте фото карты.',[[self.button('Отмена','cancel')]],parse_mode='HTML')
        else:
            state['step']='review';self.set_state(state)
            self.reply(f"Сохранить реквизиты?\n{state['values']['label']}\n{state['values']['details']}",[[self.button('Сохранить реквизиты','budget_saved_save',flow=state['flow'])],[self.button('Отмена','cancel')]])
        return True

    def budget_saved_action(self,data,token):
        kind=data['kind']
        if kind=='budget_saved_menu':self.budget_saved_menu();return True
        if kind=='budget_saved_add':self.budget_saved_add();return True
        if kind=='budget_saved_delete':
            rows=self.api('GET','bot/payout-details');row=next((row for row in rows if row['id']==data['id']),None)
            if not row:raise ApiError(404,'Реквизиты уже удалены. Откройте список заново.')
            self.reply('Удалить сохранённые реквизиты «'+row['label']+'»? Поданные заявки останутся без изменений.',[[self.button('Подтвердить удаление','budget_saved_delete_confirm',id=row['id'])],[self.button('Отмена','budget_saved_menu')]])
            return True
        if kind=='budget_saved_delete_confirm':
            self.api('DELETE',f"bot/payout-details/{data['id']}");self.store.done(token);self.budget_saved_menu();return True
        if kind=='budget_saved_save':
            state=self.store.state(self.tid,self.generation)
            if not state or state.get('kind')!='budget_saved_form' or state.get('step')!='review' or state.get('flow')!=data['flow']:
                raise ApiError(409,'Ввод устарел. Откройте сохранённые реквизиты заново.')
            self.request_id='saved-payout:'+state['flow']
            self.api('POST','bot/payout-details',state['values']);self.store.done(token);self.budget_saved_menu();return True
        if kind in {'budget_privacy_page','budget_privacy_accept'}:
            state=self.store.state(self.tid,self.generation)
            if not state or state.get('kind')!='budget_privacy' or state.get('flow')!=data['flow']:raise ApiError(409,'Условия устарели. Откройте бюджет заново.')
            if kind=='budget_privacy_page':self.budget_privacy(state['scope'],state['next'],data['page'],state['flow'],state.get('draft'));return True
            if state['page']!=state['last']:raise ApiError(409,'Сначала ознакомьтесь с условиями и правами.')
            self.api('POST','bot/personal-data/consents',{'scope':state['scope'],'version':state['version']})
            self.store.done(token)
            if state['next']=='resume_budget' and state.get('draft'):self.budget_review(state['draft'])
            elif state['next']=='saved_add':self.budget_saved_add()
            else:self.budget_menu()
            return True
        if kind=='budget_privacy_controls':
            status=self.api('GET','bot/personal-data/me')
            self.set_state(None)
            self.reply('Персональные данные. Контакт оператора: '+status['policy']['contact']+'\nОтзыв согласия на хранение реквизитов удаляет шаблоны. Отзыв основного согласия также закрывает доступ к сайту до нового согласия. Для удаления остальных данных обратитесь к оператору.',[
                [self.button('Прочитать условия','budget_privacy_read')],
                [self.button('Отозвать хранение реквизитов','budget_privacy_revoke',scope='saved_details')],
                [self.button('Отозвать основное согласие','budget_privacy_revoke',scope='service')],
                [self.button('Назад в бюджет','budget_menu')]])
            return True
        if kind=='budget_privacy_read':self.budget_privacy();return True
        if kind=='budget_privacy_revoke':
            self.reply('Подтвердить отзыв согласия? Все сохранённые реквизиты будут удалены. Это не удаляет данные уже поданных заявок.',[[self.button('Подтвердить отзыв','budget_privacy_revoke_confirm',scope=data['scope'])],[self.button('Отмена','budget_privacy_controls')]])
            return True
        if kind=='budget_privacy_revoke_confirm':
            self.api('DELETE','bot/personal-data/consents/'+data['scope']);self.store.done(token);self.set_state(None)
            self.reply('Согласие отозвано. Сохранённые реквизиты удалены. По вопросам остальных данных обратитесь к оператору.',[[self.button('Главное меню','menu')]])
            return True
        if kind in {'budget_saved_choose','budget_new_details','budget_change_details'}:
            state=self.store.state(self.tid,self.generation)
            if not state or state.get('kind')!='budget_form' or state.get('flow')!=data['flow'] or state.get('step') not in {'payout_details','review'}:
                raise ApiError(409,'Эта заявка устарела. Начните заново.')
            state['values'].pop('saved_payout_id',None);state['values'].pop('payout_details',None);state.pop('selected_payout_details',None)
            if kind=='budget_saved_choose':
                row=next((row for row in self.api('GET','bot/payout-details') if row['id']==data['id']),None)
                if not row:
                    self.notice='Реквизиты удалены. Выберите другие или введите новые.'
                    self.budget_prompt(state,'payout_details');return True
                state['values']['saved_payout_id']=row['id'];state['selected_payout_details']=row['details']
                state.pop('manual_payout',None)
                if state['values'].get('telegram_file_id'):self.budget_review(state)
                else:self.budget_prompt(state,'photo')
            else:
                state['manual_payout']=kind=='budget_new_details'
                self.budget_prompt(state,'payout_details')
            return True
        return False
