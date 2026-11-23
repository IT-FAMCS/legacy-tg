"""Durable private-chat finance request wizard and current cashier actions."""
import secrets
from decimal import Decimal, InvalidOperation
from clients import ApiError


def parse_amount(text):
    try:
        amount=Decimal(text.replace(',', '.'))
        if not amount.is_finite() or not 0 < amount <= Decimal('9999999999.99') or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError('Введите положительную сумму BYN, не более двух знаков после запятой.')
    return format(amount, '.2f')


class BudgetFormMixin:
    def budget_menu(self):
        self.set_state(None)
        self.reply('Бюджет: выберите тип заявки.',[
            [self.button('Запросить возврат денег','budget_start',request_type='reimbursement')],
            [self.button('Запросить пополнение','budget_start',request_type='add_funds')],
            [self.button('Главное меню','menu')]])

    def budget_prompt(self,state,step):
        state.update(kind='budget_form',step=step)
        self.set_state(state)
        prompts={'description':'Напишите, на что потрачены деньги.' if state['values']['request_type']=='reimbursement' else 'Опишите пополнение бюджета.',
                 'amount':'Введите сумму в BYN (например, 12,34).',
                 'payout_details':'Куда отправить деньги? Укажите реквизиты (до 500 символов).',
                 'photo':'Отправьте фото чека.' if state['values']['request_type']=='reimbursement' else 'Отправьте фото подтверждения или /skip, чтобы пропустить.'}
        self.reply(prompts[step],[[self.button('Отмена','cancel')]])

    def budget_review(self,state):
        state['step']='review'; self.set_state(state)
        values=state['values']
        lines=['Проверьте заявку:','Возврат денег' if values['request_type']=='reimbursement' else 'Пополнение бюджета',
               values['description'],values['amount']+' BYN']
        if values.get('payout_details'): lines.append('Реквизиты: '+values['payout_details'])
        lines.append('Фото приложено' if values.get('telegram_file_id') else 'Без фото')
        self.reply('\n'.join(lines),[[self.button('Подтвердить','budget_submit',flow=state['flow'])],
            [self.button('Заполнить заново','budget_start',request_type=values['request_type']),self.button('Отмена','cancel')]])

    def budget_message(self,message):
        state=self.store.state(self.tid,self.generation)
        if not state or not state['kind'].startswith('budget_'):return False
        if state.get('_menu_message_id'):self.menu_message_id=state['_menu_message_id']
        elif state.get('_menu_session'):self.new_menu=True;self.menu_session=state['_menu_session']
        text=(message.get('text') or '').strip()
        step=state['step']
        if state['kind']=='budget_amount_edit':
            try:
                amount=parse_amount(text)
                self.api('PATCH',f"bot/budget/requests/{state['id']}",{'amount':amount})
            except ValueError as error:self.reply(str(error));return True
            self.set_state(None);self.reply('Сумма обновлена. Казначеи получат исправленную заявку.');return True
        if step=='review':self.budget_review(state);return True
        values=state['values']
        if step=='photo':
            photos=message.get('photo') or []
            if photos:values['telegram_file_id']=photos[-1]['file_id']
            elif text=='/skip' and values['request_type']=='add_funds':pass
            else:self.reply('Нужно фото, отправленное как изображение. /cancel — отмена.');return True
            self.budget_review(state);return True
        if step=='amount':
            try:values['amount']=parse_amount(text)
            except ValueError as error:self.reply(str(error));return True
            self.budget_prompt(state,'payout_details' if values['request_type']=='reimbursement' else 'photo');return True
        limit=1500 if step=='description' else 500
        if not text or len(text)>limit:self.reply(f'Введите текст до {limit} символов.');return True
        values[step]=text
        self.budget_prompt(state,'amount' if step=='description' else 'photo')
        return True

    def budget_action(self,data,token):
        kind=data['kind']
        if not kind.startswith('budget_'):return False
        if kind=='budget_menu':self.budget_menu();return True
        if kind=='budget_start':
            self.budget_prompt({'flow':secrets.token_hex(8),'values':{'request_type':data['request_type']}},'description');return True
        if kind=='budget_submit':
            state=self.store.state(self.tid,self.generation)
            if not state or state.get('flow')!=data['flow'] or state.get('step')!='review':
                self.reply('Черновик устарел. Откройте /budget.');return True
            self.request_id='budget-submit:'+state['flow']
            try:self.api('POST','bot/budget/requests',state['values'])
            except ApiError as error:
                if error.status not in {409,422,503}:raise
                self.notice=str(error);self.budget_review(state);return True
            self.store.done(token);self.set_state(None);self.main_menu('Заявка сохранена и передана казначеям. Результат придёт сюда.');return True
        rid=data['id']
        row=self.api('GET',f'bot/budget/requests/{rid}')
        if row['status']!='pending':self.reply('Заявка уже обработана.');return True
        if kind=='budget_edit_amount':
            self.set_state({'kind':'budget_amount_edit','step':'amount','id':rid})
            self.reply(f"Текущая сумма: {row['amount']} BYN. Введите исправленную сумму.");return True
        if kind=='budget_open':
            self.reply(f"Заявка №{rid}: {row['applicant_name']}\n{row['description']}\n{row['amount']} BYN. Используйте кнопки в сообщении казначею; оно обновляется автоматически.")
            return True
        if kind=='budget_decide':
            payload={key:data[key] for key in ('decision','account','expected_amount') if key in data}
            try:self.api('POST',f'bot/budget/requests/{rid}/decision',payload)
            except ApiError as error:
                if error.status!=409:raise
                self.reply(str(error),[[self.button('Обновить заявку','budget_open',id=rid)]]);return True
            self.store.done(token);self.reply('Заявка обработана. Участнику будет отправлено уведомление.');return True
        return True
