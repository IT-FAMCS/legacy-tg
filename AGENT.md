# legacy-tg — Наследие ФПМИ

Independent repository: IT-FAMCS/legacy-tg. Python 3.13 standard library only.
`bot.py` configures the Telegram bot name/menu and serves internal `/healthz`.
No update consumer, database or business commands yet. Existing web authentication stays.
Run checks: `python -m unittest discover -s tests -v`.
Deploy: main → GitHub Actions → GHCR → existing host watchtower.
See DEPLOY.md. Do not commit BOT_TOKEN/.env or log Telegram request URLs.
Do not alter backend DB or add Telegram auto-login without a separate requirement.
