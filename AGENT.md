# legacy-tg — Наследие ФПМИ
Independent repository IT-FAMCS/legacy-tg. Python 3.13 standard library only.
Private-chat controller covers own tasks/completion, full equipment flows and event responsibility zones.
Backend handles binding, current permissions, replay receipts and durable task notifications.
Never trust contact @username for identity; use confirmed numeric Telegram binding. Never store Legacy passwords/member JWTs or log BOT_TOKEN/URLs/keys/chat contents.
Run `python -m unittest discover -s tests -v`; optional cross-repo integration in tests/integration_backend.py. Fake Telegram only in tests.
Runtime uses long polling + independent delivery, persistent SQLite bot state on /data volume, and internal /healthz. Refuse existing webhooks rather than changing them.
Deployment follows main → Actions → GHCR → watchtower; see DEPLOY.md. No production database resets.
