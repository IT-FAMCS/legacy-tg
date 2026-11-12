# Деплой legacy-tg

Схема соответствует бэкенду: GitHub Actions → GHCR → Docker → watchtower.
Продовый compose и настройки watchtower хранятся на сервере отдельно от репозиториев.
Новый контейнер watchtower сам не создаёт — первый запуск выполняется вручную.

## 1. Telegram

Создайте бота в @BotFather, если он ещё не создан. Выберите свободный username.
Сохраните выданный токен в `.env` на сервере как `BOT_TOKEN`.
При запуске сервис задаст имя «Наследие ФПМИ» и default menu button с Mini App
`https://famcs-legacy.by`. Для запуска через меню дополнительных настроек BotFather не нужно.

Опционально включите Main Mini App в @BotFather (Bot Settings → Configure Mini App)
с тем же URL, чтобы кнопка запуска появилась также в профиле бота.
Эту настройку профиля данный сервис не выполняет.

## 2. GitHub / GHCR

Push в main запускает тесты, затем сборку и публикацию образа:

- `ghcr.io/it-famcs/legacy-tg:latest`
- `ghcr.io/it-famcs/legacy-tg:<commit-sha>`

Проверьте успешный workflow во вкладке Actions. Нужны разрешённые Actions
и право workflow на запись Packages. Для анонимного скачивания сделайте пакет public
в IT-FAMCS → Packages → legacy-tg → Package settings.
Для private-пакета настройте `docker login ghcr.io` на хосте и registry auth watchtower
по принятой на сервере схеме; пароль не включайте в compose/git.

## 3. Первый запуск на сервере

```bash
git clone https://github.com/IT-FAMCS/legacy-tg.git
cd legacy-tg
cp .env.example .env
chmod 600 .env
# Заполните BOT_TOKEN в .env редактором на сервере.
docker compose pull bot
docker compose up -d --no-build bot
docker compose ps
docker compose logs --tail=50 bot
```

Можно добавить сервис из `compose.yaml` в существующий продовый стек вместо отдельного
compose-проекта. Сохраните передачу `.env`/BOT_TOKEN и restart policy.
Публиковать порты, добавлять домен, Caddy route, БД или volume не требуется:
бот обращается к Telegram по исходящему HTTPS, а Mini App открывает уже размещённый сайт.

Существующий watchtower должен видеть контейнер `legacy-tg`.
Если watchtower фильтрует по именам, добавьте `legacy-tg` в его список;
при `--label-enable` метка уже есть в compose. Не запускайте второй watchtower.

## 4. Проверка

```bash
docker inspect --format '{{.State.Health.Status}}' legacy-tg
docker compose exec bot python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read().decode())"
```

Ожидаются `healthy` и `{"status":"ok"}`.
Откройте личный чат с ботом в Telegram и нажмите «Открыть Наследие».
Проверьте загрузку сайта и обычный вход на телефоне и в Telegram Desktop.
Токен/доступ к серверу необходимы для этой живой проверки; unit-тесты их не заменяют.
Если для конкретного чата ранее задавалась своя кнопка меню, она может перекрыть default.

## Обновление и откат

Watchtower обновляет образ при разрешённых серверных настройках.
Вручную: `docker compose pull bot`, затем `docker compose up -d --no-build bot`.
Чтобы откатить код, замените `:latest` в compose на SHA успешного образа,
временно отключите watchtower для этого контейнера и пересоздайте сервис.
При смене токена обновите `.env` и выполните `docker compose up -d --force-recreate bot`.
Секретов в образе нет. Не передавайте токен в URL команд или сообщения.
