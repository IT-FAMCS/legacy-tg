# Наследие ФПМИ

Approved scope: independent IT-FAMCS/legacy-tg repository, main branch, pushed to GitHub.
Python 3.13 standard library; no business commands, polling, database or backend credentials.
On startup set the bot name to ?Наследие ФПМИ? and its default private-chat menu
button to a Telegram web_app opening https://famcs-legacy.by.
Existing web login remains; no Telegram identity is trusted or mapped to accounts.
Token is supplied only through BOT_TOKEN on the host. Fail closed on invalid config/API failure.
Docker image published by GitHub Actions to ghcr.io/it-famcs/legacy-tg,
latest and commit SHA tags. Existing server watchtower updates an explicitly added service.
Health endpoint is internal; healthy means startup configuration succeeded and process responds.
Alternatives: polling adds unnecessary message handling; webhook adds public infrastructure.
Chosen: configure menu once at startup, then run a minimal health server.
Host deployment and live Telegram verification require the owner's bot token/server access.
