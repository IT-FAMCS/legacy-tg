"""Idempotent, best-effort cosmetic settings; authentication stays mandatory."""
import logging
from urllib.parse import urlsplit, urlunsplit

from clients import TelegramError, TransportError

LOG = logging.getLogger(__name__)


def ensure_setting(client, getter, setter, payload, matches):
    """Try once per startup; never sleep/retry a cosmetic request in the boot path."""
    try:
        if not matches(client.call(getter, {})):
            if client.call(setter, payload) is not True:
                raise RuntimeError(f'Telegram {setter} rejected the configuration')
    except TelegramError as error:
        if error.code != 429 and not 500 <= error.code < 600:
            raise
        LOG.warning('Optional Telegram %s skipped for this startup: code=%s retry_after=%s',
                    setter, error.code, error.retry_after)
    except TransportError:
        # Exception text may include credentials in third-party transports.
        LOG.warning('Optional Telegram %s skipped for this startup: transport unavailable', setter)


def canonical_url(url):
    # Telegram may add a slash to an empty root path. /path and /path/ differ.
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, parts.fragment))


def menu_matches(actual, expected):
    if not isinstance(actual, dict) or not isinstance(actual.get('web_app'), dict):
        return False
    return (actual.get('type') == expected['type']
            and actual.get('text') == expected['text']
            and canonical_url(actual['web_app'].get('url')) == canonical_url(expected['web_app']['url']))
