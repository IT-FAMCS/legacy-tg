# Деплой Telegram-интеграции

Для этой версии нужны согласованные backend, frontend и bot из веток `feat/telegram-integration`. Изменения подготовлены локально; публикация и отправка настоящим пользователям не выполняются тестами.

1. Развернуть backend с добавленными таблицами (обычный `create_all`, без удаления БД). Передать TELEGRAM_SERVICE_KEY, TELEGRAM_BOT_USERNAME, TELEGRAM_NOTIFICATION_TIMEZONE и TELEGRAM_REMINDER_HOUR. BOT_TOKEN backend не нужен.
2. Развернуть frontend с компонентом привязки в собственном профиле и зонами мероприятий.
3. На хосте бота заполнить `.env`: существующий BOT_TOKEN, BACKEND_URL, WEBAPP_URL, тот же TELEGRAM_SERVICE_KEY. Не хранить секреты в репозитории/Actions.
4. Обновить compose: добавить именованный постоянный том `bot-data:/data`, сохранить пользователя 10001, read_only, restart и watchtower labels. Бот использует свою SQLite БД, не файл backend.
5. Убедиться, что для токена нет другого polling consumer/webhook. Код намеренно не вызывает deleteWebhook; любые изменения webhook делает оператор явно.
6. Выполнить `docker compose pull bot` и `docker compose up -d --no-build bot` после публикации подходящего образа, либо локально собрать `docker compose up -d --build bot`.
7. Проверить `docker compose ps`, `/healthz`, отсутствие ошибок service-auth. Каждый пользователь сам проходит привязку через сайт; поле Telegram контактов не мигрируется в авторизацию.

GitHub Actions на main/master проверяет код и публикует `ghcr.io/it-famcs/legacy-tg:latest` + SHA; watchtower использует существующую схему обновления. Публикация ветки интеграции не является автоматическим развёртыванием в этой работе.

При откате не удаляйте `bot-data` и backend-БД. Можно остановить бот: уведомления останутся в очереди, просроченные календарные напоминания не будут отправлены задним числом. При замене сервисного ключа меняйте его на обеих сторонах. Бот не хранит паролей «Наследия» или долгоживущих пользовательских JWT.
