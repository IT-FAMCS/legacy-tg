import sqlite3
import unittest
from unittest.mock import Mock, patch

import bot
from clients import TelegramError, TransportError
from runtime import COMMANDS, Runtime
from telegram_setup import ensure_setting, menu_matches


class FakeTelegram:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, method, payload):
        self.calls.append((method, payload))
        result = self.responses.get(method, True)
        if isinstance(result, Exception):
            raise result
        return result


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.settings = bot.Settings('123:private-token', 'https://example.org')
        self.menu = {'type': 'web_app', 'text': 'Открыть Наследие',
                     'web_app': {'url': 'https://example.org/'}}

    def configure(self, responses):
        client = FakeTelegram(responses)
        with patch('bot.TelegramClient', return_value=client):
            bot.configure_bot(self.settings)
        return client

    def test_matching_profile_does_not_write(self):
        client = self.configure({'getMyName': {'name': self.settings.name},
                                 'getChatMenuButton': self.menu})
        self.assertEqual([m for m, _ in client.calls], ['getMyName', 'getChatMenuButton'])

    def test_only_changed_name_is_written(self):
        client = self.configure({'getMyName': {'name': 'old'}, 'getChatMenuButton': self.menu})
        self.assertEqual([m for m, _ in client.calls], ['getMyName', 'setMyName', 'getChatMenuButton'])

    def test_only_changed_menu_is_written(self):
        client = self.configure({'getMyName': {'name': self.settings.name},
                                 'getChatMenuButton': {'type': 'default'}})
        self.assertEqual([m for m, _ in client.calls], ['getMyName', 'getChatMenuButton', 'setChatMenuButton'])

    def test_name_rate_limit_does_not_skip_menu_or_sleep(self):
        with self.assertLogs('telegram_setup', level='WARNING') as logs:
            client = self.configure({'getMyName': {'name': 'old'},
                'setMyName': TelegramError(429, 84407), 'getChatMenuButton': {'type': 'default'}})
        self.assertEqual([m for m, _ in client.calls],
                         ['getMyName', 'setMyName', 'getChatMenuButton', 'setChatMenuButton'])
        self.assertIn('84407', ' '.join(logs.output))
        self.assertNotIn('private-token', ' '.join(logs.output))

    def test_temporary_get_failure_never_blindly_writes(self):
        for failure in (TelegramError(429, 60), TelegramError(502), TransportError('private-token')):
            with self.subTest(failure=type(failure).__name__), self.assertLogs('telegram_setup') as logs:
                client = self.configure({'getMyName': failure, 'getChatMenuButton': self.menu})
            self.assertEqual([m for m, _ in client.calls], ['getMyName', 'getChatMenuButton'])
            self.assertNotIn('private-token', ' '.join(logs.output))

    def test_auth_and_configuration_errors_remain_fatal(self):
        for code in (400, 401, 403):
            with self.subTest(code=code), self.assertRaises(TelegramError):
                self.configure({'getMyName': TelegramError(code)})

    def test_false_setter_result_is_fatal(self):
        with self.assertRaises(RuntimeError):
            self.configure({'getMyName': {}, 'setMyName': False})

    def test_menu_normalizes_root_only(self):
        self.assertTrue(menu_matches(self.menu, {**self.menu, 'web_app': {'url': 'https://example.org'}}))
        self.assertFalse(menu_matches({**self.menu, 'web_app': {'url': 'https://example.org/path/'}},
                                     {**self.menu, 'web_app': {'url': 'https://example.org/path'}}))
        self.assertFalse(menu_matches({'web_app': None}, self.menu))

    def test_full_disk_stops_before_telegram_configuration(self):
        env = {'BOT_TOKEN': '123:private-token', 'TELEGRAM_SERVICE_KEY': 'x' * 40}
        with patch.dict('os.environ', env, clear=True), \
             patch('store.Store', side_effect=sqlite3.OperationalError('disk full')), \
             patch('bot.configure_bot') as configure, patch('bot.ThreadingHTTPServer') as server, \
             self.assertLogs('bot', level='ERROR'):
            self.assertEqual(bot.main(), 1)
        configure.assert_not_called()
        server.assert_not_called()


class RuntimeSetupTests(unittest.TestCase):
    def runtime(self, responses):
        client = FakeTelegram(responses)
        return Runtime(Mock(), client, Mock(), 'https://example.org'), client

    def test_matching_commands_not_written(self):
        runtime, client = self.runtime({'getWebhookInfo': {'url': ''}, 'getMyCommands': COMMANDS})
        runtime.check_startup()
        self.assertEqual([m for m, _ in client.calls], ['getWebhookInfo', 'getMyCommands'])

    def test_command_rate_limit_still_starts_both_workers(self):
        runtime, client = self.runtime({'getWebhookInfo': {'url': ''}, 'getMyCommands': [],
                                       'setMyCommands': TelegramError(429, 84000)})
        with patch('runtime.threading.Thread') as thread, self.assertLogs('telegram_setup'):
            runtime.start()
        self.assertEqual(thread.call_count, 2)
        self.assertEqual(thread.return_value.start.call_count, 2)
        self.assertEqual([m for m, _ in client.calls], ['getWebhookInfo', 'getMyCommands', 'setMyCommands'])

    def test_webhook_errors_and_existing_webhook_prevent_workers(self):
        for response in ({'url': 'https://existing'}, None, TelegramError(401),
                         TelegramError(429, 60), TransportError('unavailable')):
            runtime, client = self.runtime({'getWebhookInfo': response})
            with self.subTest(response=type(response).__name__), patch('runtime.threading.Thread') as thread:
                with self.assertRaises(RuntimeError):
                    runtime.start()
                thread.assert_not_called()
                self.assertEqual([m for m, _ in client.calls], ['getWebhookInfo'])


if __name__ == '__main__':
    unittest.main()
