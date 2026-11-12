import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import bot


class SettingsTests(unittest.TestCase):
    def test_default_site_and_bot_name(self):
        config = bot.Settings.from_env({"BOT_TOKEN": "123:secret"})
        self.assertEqual(config.webapp_url, "https://famcs-legacy.by")
        self.assertEqual(config.name, "Наследие ФПМИ")

    def test_missing_or_placeholder_token_is_rejected(self):
        for token in ("", "   ", "replace-me", "123:with space"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                bot.Settings.from_env({"BOT_TOKEN": token})

    def test_insecure_or_invalid_webapp_urls_are_rejected(self):
        for url in ("http://example.com", "https://", "https://u:p@example.com", "https://example.com/#fragment", "https://example.com/ bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bot.Settings.from_env({"BOT_TOKEN": "123:secret", "WEBAPP_URL": url})

    def test_invalid_port_is_rejected(self):
        for port in ("0", "65536", "abc"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                bot.Settings.from_env({"BOT_TOKEN": "123:secret", "PORT": port})


class TelegramTests(unittest.TestCase):
    def test_startup_sends_name_and_default_webapp_menu_only(self):
        requests = []
        def respond(request, timeout):
            requests.append((request.full_url.rsplit("/", 1)[-1], json.loads(request.data)))
            self.assertEqual(request.get_method(), "POST")
            self.assertGreater(timeout, 0)
            return io.BytesIO(b'{"ok":true,"result":true}')
        with patch("bot.urlopen", side_effect=respond):
            bot.configure_bot(bot.Settings.from_env({"BOT_TOKEN": "123:secret"}))
        self.assertEqual(requests, [
            ("setMyName", {"name": "Наследие ФПМИ"}),
            ("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Открыть Наследие", "web_app": {"url": "https://famcs-legacy.by"}}}),
        ])

    def test_api_rejection_is_fatal_and_does_not_expose_token(self):
        with patch("bot.urlopen", return_value=io.BytesIO(b'{"ok":false,"description":"123:secret"}')):
            with self.assertRaises(RuntimeError) as error:
                bot.call_api("123:secret", "setMyName", {"name": "test"})
        self.assertNotIn("secret", str(error.exception))

    def test_transport_failures_are_sanitized(self):
        errors = [URLError("https://api.telegram.org/bot123:secret"),
                  HTTPError("https://api.telegram.org/bot123:secret", 401, "Unauthorized", {}, None),
                  TimeoutError("123:secret")]
        for failure in errors:
            with self.subTest(failure=type(failure).__name__), patch("bot.urlopen", side_effect=failure):
                with self.assertRaises(RuntimeError) as error:
                    bot.call_api("123:secret", "setMyName", {})
                self.assertNotIn("secret", str(error.exception))

    def test_malformed_response_is_sanitized(self):
        for body in (b'123:secret', b'[]', b'{"ok":true,"result":false}'):
            with self.subTest(body=body), patch("bot.urlopen", return_value=io.BytesIO(body)):
                with self.assertRaises(RuntimeError):
                    bot.call_api("123:secret", "setMyName", {})

    def test_failed_configuration_never_starts_health_server(self):
        with patch.dict("os.environ", {"BOT_TOKEN": "123:secret"}, clear=True), patch("bot.configure_bot", side_effect=RuntimeError("failed")), patch("bot.ThreadingHTTPServer") as server:
            with self.assertLogs("bot", level="ERROR"):
                self.assertEqual(bot.main(), 1)
            server.assert_not_called()


class HealthTests(unittest.TestCase):
    def test_health_and_unknown_route(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), bot.HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_port)
            with urlopen(base + "/healthz", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {"status": "ok"})
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/", timeout=2)
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
