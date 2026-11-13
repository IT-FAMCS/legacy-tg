# Telegram Chat Integration Implementation Plan
> Implement inline with superpowers:executing-plans; test-driven domain changes; whole-branch review at the end.
**Goal:** Securely linked chat-only tasks and full equipment operations, with durable assignment/removal/deadline notifications.
**Architecture:** Add Telegram identities/link challenges/delivery queue/request receipts in backend. Reuse equipment router with alternate actor dependency/prefix; constrain task router to own reads/completion. Standard-library bot has transport, durable conversation/update store, chat controller and notification worker. React profile has linking controls.
**Tech stack:** Existing FastAPI/Pydantic/SQLAlchemy SQLite, React/TypeScript, Python standard library bot. No Telegram token in backend/frontend.
**Spec:** ../specs/2026-11-13-telegram-chat-design.md (user approved).
## Global constraints
Private chat only; numeric Telegram ID bound with explicit website confirmation. Own profile only. Every operation checks current binding/account/role. Bot shares domain operations with web. No real outbound Telegram tests or deployment. Use isolated databases and fresh feat/telegram-integration worktrees.
## Review focus
Concurrent link consumption and uniqueness; delayed callback ownership/revocation; atomic mutation replay receipts; outbox lease retry and cross-binding privacy; cancelled/stale equipment flows and Telegram UTF-16 message limits.
## Task 1: Backend identity and shared bot actions
Files: models.py, telegram_auth.py, telegram_links.py, tasks.py, equipment.py, main.py; tests/test_telegram.py.
Interfaces: member GET /api/telegram/link, POST /link (new challenge), POST /link/confirm, DELETE /link. Service POST /api/telegram/bot/claim. Bot GET /api/telegram/bot/me; POST /unlink; /equipment mirrors existing equipment operations; /tasks lists own active, reads own, PATCH completion only. Service authentication header X-Telegram-Service-Key; actor X-Telegram-User-ID; mutations X-Telegram-Request-ID, cached atomically with response fingerprint and binding generation.
- [x] Write and run failing identity/ownership/role/replay tests.
- [x] Implement additive models and link/actor APIs, parameterize shared routers and atomic replay handling.
- [x] Run new tests and full backend suite.
## Task 2: Notification queue
Files: telegram_notifications.py, tasks.py history hook, telegram_links.py service endpoints; tests/test_telegram_notifications.py.
Interfaces: POST /api/telegram/service/notifications/claim (lease batch); POST /notifications/{id}/ack with lease token and delivery outcome/retry delay. Assignment event written with task transaction; reminders generated for tomorrow at configured Europe/Moscow 10:00, per task/user/deadline unique key. Validate live access/binding before handoff; ack generation-checked; expired leases retry.
- [x] Write failing assignment delta, reminder time/dedup/obsolete/lease/rebind tests.
- [x] Implement queue, retries, suppression and hook; verify full suite.
## Task 3: Chat bot runtime
Files: bot.py, clients.py, store.py, chat.py, worker.py, Dockerfile, compose.yaml, .env.example, tests/test_chat.py, tests/test_runtime.py.
Interfaces: backend client calls above API with sender identity and stable update/callback mutation ID. Durable SQLite store stores update offset, processed IDs, expiring callback action tokens bound to user/binding generation and conversation drafts. /start,/tasks,/equipment,/link,/unlink,/cancel,/help; inline pagination/search/wizards. Worker sends leased messages, acknowledges and handles rate limits/blocked chats.
- [x] Write failing fake-transport chat/runtime tests for private sender, link prompt, own completion, equipment create/edit/take/transfer/history/archive, cancel/stale/duplicate actions.
- [x] Implement durable runtime, graceful shutdown and readiness; reject existing webhook.
- [x] Run full bot suite and compile checks.
## Task 4: Frontend linking and integration
Files: src/api/telegram.ts, components/TelegramLink.tsx, profile integration/styles; tests and browser smoke fixture.
- [x] Test and implement link status, challenge, pending identity display, explicit confirm/cancel/unlink; only own identity controls.
- [x] Check frontend tests/lint/build and browser link workflow with fake bot API, no external message delivery.
- [x] Run review of all changes, fix material issues with regression tests. Document config/ops/limitations; commit local branches without merge/deploy.

## Task 5: Event responsibility zones (added requirement)
Files: backend responsibility_zones.py/models.py/main.py/tasks.py guard; frontend features/zones and card tab; bot chat zones mixin; tests and browser fixture.
Interface: GET/POST /api/cards/{card_id}/responsibility-zones; PATCH/DELETE /{zone_id}; POST /clear; GET /history. Writes include board_version and zone version where applicable. Bot mirrors under /api/telegram/bot/events/{card_id}/responsibility-zones, plus GET /events. All writes require current can_edit_cards and card access. No profile exposure.
- [x] Write failing API tests for event-only scope, permissions, headcount/lead uniqueness, stale writes, atomic clear/history, bot replay and card lifecycle.
- [x] Implement shared domain, web tab/forms/counters, and chat event/zone/staffing flows.
- [x] Verify all suites and end-to-end chat/browser flows including clear confirmation and role revocation.
