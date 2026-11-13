"""Deployment shell: configure the Telegram Mini App menu, then serve health."""

import json
import logging
import os
import re
import signal
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    webapp_url: str
    name: str = "Наследие ФПМИ"
    port: int = 8080

    @classmethod
    def from_env(cls, env):
        token = env.get("BOT_TOKEN", "").strip()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("Set BOT_TOKEN to the token issued by BotFather")
        url = env.get("WEBAPP_URL", "https://famcs-legacy.by").strip()
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment
                or any(char.isspace() for char in url)):
            raise ValueError("WEBAPP_URL must be an HTTPS URL without credentials or fragment")
        # Accessing port also rejects malformed or out-of-range URL ports.
        parts.port
        port = int(env.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(token=token, webapp_url=url, port=port)


def call_api(token, method, payload):
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except (URLError, OSError, ValueError):
        # HTTP exception URLs and response descriptions can contain the token.
        raise RuntimeError(f"Telegram {method} failed; check token and network") from None
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("result") is not True:
        raise RuntimeError(f"Telegram {method} rejected the configuration")


def configure_bot(settings):
    call_api(settings.token, "setMyName", {"name": settings.name})
    call_api(settings.token, "setChatMenuButton", {
        "menu_button": {
            "type": "web_app",
            "text": "Открыть Наследие",
            "web_app": {"url": settings.webapp_url},
        },
    })


class HealthHandler(BaseHTTPRequestHandler):
    readiness = staticmethod(lambda: True)
    def do_GET(self):
        found = self.path == "/healthz"
        healthy = found and self.readiness()
        body = b'{"status":"ok"}' if healthy else b'{"error":"not found"}'
        self.send_response(200 if healthy else 503 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = Settings.from_env(os.environ)
        configure_bot(settings)
        from clients import BackendClient, TelegramClient
        from store import Store
        from runtime import Runtime
        backend = BackendClient(os.getenv('BACKEND_URL', settings.webapp_url), os.getenv('TELEGRAM_SERVICE_KEY', ''))
        store = Store(os.getenv('BOT_DB_PATH', '/data/bot.sqlite3'))
        runtime = Runtime(backend, TelegramClient(settings.token), store, settings.webapp_url,
                          int(os.getenv('NOTIFICATION_POLL_SECONDS', '10')))
        runtime.start()
        HealthHandler.readiness = staticmethod(runtime.ready)
    except (ValueError, RuntimeError) as error:
        LOG.error("%s", error)
        return 1

    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    LOG.info("Telegram chat runtime started; no credentials are logged")
    try:
        with ThreadingHTTPServer(("0.0.0.0", settings.port), HealthHandler) as server:
            server.serve_forever()
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
