import unittest
from clients import ApiError
import test_budget as fixture

class SavedBackend(fixture.Backend):
    def __init__(self):
        super().__init__();self.saved=[{'id':7,'label':'Belinvest','details':'123456789'}];self.accepted=True;self.saving=True
    def request(self,method,path,body=None,**kw):
        if path=='bot/budget/requests' and method=='POST' and not self.accepted:raise ApiError(403,'Consent required')
        if path=='bot/personal-data/me':return {'service':self.accepted,'saved_details':self.saving,'policy':{'configured':True,'version':'v1','operator':'Operator','address':'Address','contact':'privacy@example.org','processors':'Hosting','retention':'Membership','sections':['Data and purposes'],'rights':['Withdrawal']}}
        if path=='bot/personal-data/consents':
            if body['scope']=='service':self.accepted=True
            else:self.saving=True
            return {'ok':True}
        if path=='bot/payout-details':
            if method=='GET':return self.saved
            row={'id':8,**body};self.saved.append(row);return row
        if path.startswith('bot/payout-details/') and method=='DELETE':
            self.saved=[row for row in self.saved if row['id']!=int(path.rsplit('/',1)[1])];return {'ok':True}
        return super().request(method,path,body,**kw)

class SavedPayoutTests(unittest.TestCase):
    setUp=fixture.BudgetChatTests.setUp
    tearDown=fixture.BudgetChatTests.tearDown
    message=fixture.BudgetChatTests.message
    click=fixture.BudgetChatTests.click
    def use_backend(self):
        self.backend=SavedBackend();self.chat.backend=self.backend
    def latest(self):return self.store.outgoing()[-1]['payload']['text']
    def test_saved_selection_and_new_unsaved_details(self):
        self.use_backend();self.message('/budget');self.click('возврат');self.message('Supplies');self.message('12')
        self.assertIn('белинвест',self.latest().lower());self.assertIn('ерип',self.latest().lower())
        self.click('Belinvest');self.message(photo=[{'file_id':'receipt'}]);self.click('Подтвердить')
        self.assertEqual(self.backend.requests[-1]['saved_payout_id'],7)
        self.assertNotIn('payout_details',self.backend.requests[-1])
        self.message('/budget');self.click('возврат');self.message('Other');self.message('10');self.message('new account')
        self.message(photo=[{'file_id':'receipt'}]);self.click('Подтвердить')
        self.assertEqual(self.backend.requests[-1]['payout_details'],'new account');self.assertEqual(len(self.backend.saved),1)
    def test_management_add_delete_and_limit_ui(self):
        self.use_backend();self.message('/budget');self.click('Сохранённые реквизиты');self.click('Добавить реквизиты')
        self.message('Other bank');self.message('Account 987');self.click('Сохранить реквизиты')
        self.assertEqual(self.backend.saved[-1]['details'],'Account 987')
        self.click('Удалить: Other bank');self.click('Подтвердить удаление');self.assertEqual(len(self.backend.saved),1)
        self.backend.saved.extend([{'id':8,'label':'B','details':'1'},{'id':9,'label':'C','details':'2'}])
        self.message('/budget');self.click('Сохранённые реквизиты');self.assertIn('3/3',self.latest())
        outgoing=self.store.outgoing()[-1]['payload'];self.assertNotIn('Добавить реквизиты',str(outgoing.get('reply_markup')))
    def test_missing_service_consent_requires_explicit_action(self):
        self.use_backend();self.backend.accepted=False;self.message('/budget')
        self.assertFalse(self.backend.accepted);self.assertIn('Operator',self.latest())
        # All conditions and rights must be shown before the accept action appears.
        for _ in range(10):
            buttons=str(self.store.outgoing()[-1]['payload'].get('reply_markup'))
            if 'Далее' not in buttons:break
            self.click('Далее')
        self.click('Согласен и продолжить');self.assertTrue(self.backend.accepted)
    def test_cancel_does_not_save_template(self):
        self.use_backend();self.message('/budget');self.click('Сохранённые реквизиты');self.click('Добавить реквизиты')
        self.message('Bank');self.message('/cancel');self.assertEqual(len(self.backend.saved),1)

    def test_stale_saved_choice_preserves_draft(self):
        self.use_backend();self.message('/budget');self.click('возврат');self.message('Keep description');self.message('12')
        self.backend.saved=[];self.click('Belinvest')
        state=self.store.state(101)
        self.assertIsNotNone(state);self.assertEqual(state['values']['description'],'Keep description')
        self.assertEqual(state['values']['amount'],'12.00');self.assertEqual(state['step'],'payout_details')
        self.message('manual account');self.assertEqual(self.store.state(101)['step'],'photo')

    def test_revocation_mid_draft_requires_reconsent_and_keeps_values(self):
        self.use_backend();self.message('/budget');self.click('возврат');self.message('Keep after revoke');self.message('12')
        self.message('manual account');self.message(photo=[{'file_id':'receipt'}])
        self.backend.accepted=False;self.click('Подтвердить')
        self.assertEqual(len(self.backend.requests),0)
        self.assertEqual(self.store.state(101)['kind'],'budget_privacy')
        for _ in range(10):
            buttons=str(self.store.outgoing()[-1]['payload'].get('reply_markup'))
            if 'Далее' not in buttons:break
            self.click('Далее')
        self.click('Согласен и продолжить')
        self.assertEqual(self.store.state(101)['step'],'review')
        self.click('Подтвердить')
        self.assertEqual(self.backend.requests[-1]['description'],'Keep after revoke')
        self.assertEqual(self.backend.requests[-1]['payout_details'],'manual account')

    def test_each_request_requires_explicit_payout_choice(self):
        self.use_backend()
        for _ in range(2):
            self.message('/budget');self.click('возврат');self.message('Supplies');self.message('12')
            state=self.store.state(101)
            self.assertEqual(state['step'],'payout_details')
            self.assertNotIn('saved_payout_id',state['values'])
            self.click('Ввести новые');self.message('one-off account')
            self.message(photo=[{'file_id':'receipt'}]);self.click('Подтвердить')
        self.assertEqual(len(self.backend.saved),1)
        self.assertTrue(all(row['payout_details']=='one-off account' for row in self.backend.requests))

    def test_policy_uses_safe_html_emphasis(self):
        self.use_backend();self.backend.accepted=False
        original=self.backend.request
        def request(method,path,body=None,**kw):
            result=original(method,path,body,**kw)
            if path=='bot/personal-data/me':result['policy']['rights']=['Вы вправе отозвать согласие <all> & удалить данные']
            return result
        self.backend.request=request
        self.message('/budget')
        payload=self.store.outgoing()[-1]['payload']
        self.assertEqual(payload.get('parse_mode'),'HTML')
        self.assertIn('<b><u>',payload['text'])
        self.assertIn('&lt;all&gt; &amp; удалить данные',payload['text'])
