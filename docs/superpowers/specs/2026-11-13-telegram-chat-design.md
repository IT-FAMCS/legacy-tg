# Telegram chat integration design

## Intent and agreed scope
The Legacy Telegram bot must support tasks and equipment entirely through messages and inline buttons in private chats. It sends assignment, responsibility-removal and day-before-deadline notifications. Backend Legacy permissions remain authoritative. The existing Mini App menu remains a convenience, not a substitute for requested chat operations.

## Approach
Use the existing Python-standard-library bot as the chat client, the existing FastAPI backend as the source of identity/permissions/business data, and a durable backend outbox for notifications. Add profile controls in the existing React frontend for secure linking. Reuse current domain operations rather than duplicate equipment/task permission rules in the bot.

Alternatives considered: Mini App equipment avoids chat forms but the user explicitly chose all operations in chat; matching a profile @username is not identity proof; collecting Legacy passwords in Telegram messages unnecessarily exposes credentials. The proposed approach uses a short-lived one-time link and explicit website confirmation instead.

## Secure account linking
1. A member logs in to the existing Legacy website and selects Connect Telegram in their OWN profile.
2. Backend issues a cryptographically random, single-use token expiring in 10 minutes; only its hash is stored. The website opens a bot /start deep link carrying that token.
3. In a private chat the bot submits the token and Telegram sender numeric ID/chat ID to the backend through an authenticated service endpoint. This creates a pending claim and does NOT grant account access.
4. Website displays the claimed Telegram identity (username/name and numeric ID) and requires explicit confirmation from the logged-in member. Confirmation atomically links the identity and consumes the pending token. An active binding is unique per Legacy user and Telegram user. An already-bound Telegram identity cannot be silently reassigned.
5. Unlinking is available on the website and through a bot command with confirmation. Relinking requires unlinking first. Pending tokens can be cancelled/replaced; expiry and consumption are atomic. No automatic match or migration from the existing editable Telegram contact field.
6. Link status/confirmation endpoints require the existing member JWT. Profile editing of another account cannot create or approve links. Bot service calls require a separately configured high-entropy shared key over HTTPS/internal trusted transport. Telegram numeric IDs are treated as identity claims only after service authentication, never accepted from a public frontend header.
7. Every bot action re-resolves the active binding and active Legacy account, then applies existing rights. Deactivation and unlinking revoke access immediately. Every delayed callback re-checks these facts. Bot commands work in private chats only; group messages and forwarded data do not establish identity.
8. The bot does not receive/store Legacy passwords or long-lived member JWTs. Tokens, link codes, bot URLs containing credentials and service keys are never logged. Rate/attempt limits apply to link claims and service-auth failures. Existing web login remains unchanged.

## Task chat flows
Main menu: My tasks, Equipment, Link/status, Open Legacy, Help.
My tasks lists assigned, unfinished, unarchived tasks that pass existing linked-card access checks. Paginate with short-lived callback buttons and provide details with title, description, departments/event, deadline and status. Open-on-site links are optional extras.
Each detail has Complete and Back buttons. Completing checks the CURRENT sender, current assignment, card access and optimistic task version; callback payload never supplies a trusted account ID. Duplicate updates must not create repeated mutations or audit events. Conflicts refresh current state and never overwrite newer data. Full task creation/editing is outside this request.

## Equipment chat flows
Expose all existing equipment operations with the same backend rights:
- list/paginate/search items, details and holder/location/time;
- Take item (records current member and server time);
- inspect immutable history;
- authorized users create/edit name and description, select another active holder or storage location, optionally specify a manual taken date, and create shared storage locations;
- authorized users delete/archive equipment with confirmation and preserved history.
Use inline menus plus bounded, cancellable step-by-step text prompts. /cancel always discards the current draft. Search/select users and places through paginated buttons, never arbitrary unchecked IDs. Store the initial version for each mutation and handle 409 by refreshing/restarting the edit rather than overwriting. Re-check rights before final submission, not only when displaying a button. Inactive existing holders remain visible and detail-only edits preserve custody as on the website.

## Notifications
Builder default, explicitly stated to the user: deadlines are calendar dates, so remind at 10:00 Europe/Moscow on the preceding calendar day. Timezone/hour are deployment configuration, not inferred from host time. No reminder for completed/archived tasks or former assignees. If the worker was down, catch up only during that preceding day; do not send obsolete reminders after the deadline day starts.

Assignment changes enqueue notices in the SAME DB transaction as the task mutation/audit: new responsible members receive You were assigned, removed members receive You are no longer responsible. Initial assignment on task creation also counts. Preserve stable task IDs and recipient IDs. No-op saves and unrelated edits produce no assignment notices.

Outbox entries carry a unique event/deduplication key, intended recipient and binding generation, scheduled time, attempt/lease/ack state. Deadline key is task + recipient + deadline date, preventing repeated reminders on periodic scans. Task deadline changes invalidate old pending reminders and allow a new one for the new date. Re-evaluate current assignment/completion/archive/access/account/binding at dispatch. Suppress superseded assignment notices. A removal notification may use the task title only when the recipient still has card access; otherwise send generic removal text without restricted task details.

Only currently linked recipients receive notifications. Do not backfill historical assignment events when an account is first linked. Unlink/relink invalidates old-generation pending deliveries; they must never reach a different chat. Unreachable/blocked bot recipients are suspended or marked undeliverable until contact is restored, without retry storms or account deactivation.

Worker claims entries with an expiring lease, honors Telegram retry_after/429, retries temporary failures with bounded backoff, and acknowledges delivery. Persist queue/leases across restarts. Do not promise exactly-once delivery: Telegram sendMessage has no idempotency key, so an ambiguous transport failure/crash after send but before ack can cause a duplicate. Deduplicate normal processing and make all domain mutations idempotent.

## Runtime and deployment
Add long polling to the existing bot and keep internal health checks. Persist Telegram update offset, conversational drafts/expiry and processed update identifiers in a small bot SQLite DB on a Docker volume (container root remains read-only). One consumer per bot token; refuse startup with an existing webhook rather than deleting it silently. Keep service polling for notifications separate from the long-poll wait so deadlines are not delayed by idle chats.

Handle Telegram message/callback size limits, escaping user-provided content, pagination, expired/deleted/stale buttons, cancelled flows, transport timeouts and graceful shutdown. Health reflects polling/worker readiness rather than merely successful menu setup.

New configuration: backend URL, shared service key, bot username for deep links, persistent bot DB path, notification timezone/hour and polling intervals. BOT_TOKEN remains only on the bot service. Backend only needs the shared service key; no direct Telegram API access or BOT_TOKEN on the frontend/backend. Never use application DB files from a different repository or mount backend DB in the bot.

Work on fresh feat/telegram-integration branches from updated backend main, frontend master and bot main. Previous task work is already merged upstream. Test locally with fake Telegram transport and temporary databases; real outbound messages/deployment are a separate final integration action requiring configured secrets.

## Validation and non-goals
Tests: linking expiry/replay/cancel/claim/confirm/uniqueness/ownership; revoked and inactive bindings; service authentication; forged/group callbacks; task ownership/card access/version/duplicates; each equipment action and role; conversation cancellation and restart; assignment delta/outbox atomicity; reminder local-date boundary/dedup/rescheduling/completion; notification retry/lease/blocked-chat behavior; cross-binding privacy.
Run all backend, bot and frontend suites, lint/build and browser-test link status/confirmation. Inspect representative chat transcripts and UI. Do not alter existing Telegram contact data, weaken web permissions, send real test notices to users, change webhook, merge or deploy as part of implementation.

## Added scope: event responsibility zones (user requested during implementation)
Only event cards gain a Responsibility zones tab; nothing is added to user profiles. Each zone has a title, required headcount, one optional lead account and several helper accounts, all shown with their existing Telegram contact field. Lead counts toward required headcount. A zone is closed only when an active lead is assigned and the total active distinct assignees reaches required headcount. Inactive historical assignments remain visible but do not fill staffing requirements; only active accounts can be newly assigned. Lead cannot also be a helper.
All users who can edit the event card AND can access it may create/edit/delete zones and assign people, both on the site and in the chat. Viewers with card access may read zones and totals. The Clear responsible people action applies to every zone of the current event, after confirmation, retaining zone names and required counts. It is atomic, version-checked and audited with before/after assignments. Zones do not produce task-assignment notices (not requested).
Use additive relational zone/helper/history/event-board-version tables. Shared API operations and Telegram request receipts apply to both clients; stale writes never overwrite newer assignments. Guard event deletion/recategorization while zones exist. Chat adds /zones plus event selection, zone details/staffing summary and cancellable creation/edit/assignment flows; this supplements rather than replaces the original task/equipment scope.
