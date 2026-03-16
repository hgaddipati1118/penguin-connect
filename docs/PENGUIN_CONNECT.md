# PenguinConnect (Mac Local Bridge)

PenguinConnect bridges messaging conversations to a user's Gmail inbox using per-conversation alias addresses.

Current implemented source adapter: Apple Messages (`iMessage`, `SMS`, `RCS`).

Planned next source adapters: WhatsApp and Telegram.

This bridge is macOS local-only and runs on `127.0.0.1`.

## Fast Path: Guided Setup CLI

```bash
cd /path/to/penguin-connect
./scripts/penguin_connect_setup.py --gmail you@gmail.com
```

The guided setup flow now prompts to review Apple Messages chats you want PenguinConnect to exclude before the first Gmail connect. It writes selections to `./.penguin_connect_excluded_chats.json` unless `PENGUIN_CONNECT_EXCLUDED_CHATS_FILE` is set.

Optional flags:

- `--yes` for non-interactive execution with default prompt answers; the optional excluded-chat review stays skipped in this mode
- `--client-secrets /abs/path/client_secret.json` to force OAuth JSON path
- `--signature-marker "External email:"` to save a custom signature/disclaimer cutoff marker into the local JSON preferences file
- `--skip-sync-smoke` to skip final sync endpoint smoke test
- `--explain-only` to print steps without executing

## Setup Order (Required Sequence)

1. Get Google OAuth client JSON.
2. Connect Gmail to local bridge (requests full Gmail mailbox access scope).
3. Run startup catch-up and verify health.

## What the Bridge Does

- Creates deterministic `conversation_id` values from Gmail account + source provider + source chat id.
- Assigns one active alias email per conversation.
- Imports Apple Messages conversations into Gmail inbox threads.
- Collapses Apple Messages direct messages across `iMessage`, `RCS`, and `SMS` into one logical conversation.
- Reads sibling Apple Messages DM routes during source-to-Gmail sync so route changes between `iMessage`, `RCS`, and `SMS` do not silently drop messages.
- Keeps Apple Messages group chats separate and uses the group title when one exists.
- Polls Gmail for replies to alias addresses and sends those replies back to the source provider.
- Mirrors Apple Messages read state back into Gmail `UNREAD` labels using the conversation unread count, so the latest synced inbound source messages clear once the conversation is read in Messages.
- Only Gmail messages from `SENT` that still target the exact conversation alias are eligible for Gmail-to-source delivery; drafts are ignored.
- Incremental Gmail reply detection keeps a per-conversation pending sent-activity marker until that conversation is actually synced, and it falls back to a recent sent-mail scan when the global Gmail history cursor has already moved past a valid alias reply.
- Sends only the latest non-quoted Gmail reply text back to Apple Messages; it does not append synthetic quoted context.
- Gmail reply cleanup is HTML-first and DOM-aware, so Gmail/Slashy quote containers and wrapped `On ... wrote:` reply headers are stripped before Apple Messages delivery whenever the underlying message body contains that structure.
- If a user-sent Gmail reply is rejected by the parser safety gate as `ambiguous_email_body`, PenguinConnect posts a rejection notice into the Gmail thread so the skip is visible instead of silent.
- Retries Gmail-to-Apple-Messages delivery up to 3 times. If the final attempt still fails, the bridge posts a `PENGUIN_CONNECT` reply into the Gmail thread containing the failed message body.
- Startup catch-up and backfill run a full self-heal sweep across all Apple Messages chats before syncing so legacy cache rows are migrated into the current canonical thread format.
- Applies sender gate:
  - connected Gmail primary address, or
  - verified Gmail send-as alias for same inbox.

## Prerequisites

- macOS 13+
- Terminal.app with Full Disk Access
- Backend running locally on `127.0.0.1:9000`
- Python deps installed in `server/venv`

Important:

- run setup and bridge commands from `Terminal.app`
- Full Disk Access is required to read `~/Library/Messages/chat.db`
- macOS path: `System Settings -> Privacy & Security -> Full Disk Access -> Terminal`

Install backend deps if needed:

```bash
cd /path/to/penguin-connect/server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Optional reply-cleanup setting:

- `PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE`
  - optional path to a local JSON file with a `signature_markers` array
  - default path: `./.penguin_connect_signature_markers.json`
  - see `./signature_markers.example.json` for the file format
  - when a reply line starts with one of those markers, PenguinConnect strips that line and everything after it before sending to Apple Messages
  - useful for recurring legal disclaimers, CRM footers, or signatures the built-in parser misses

Optional chat-exclusion setting:

- `PENGUIN_CONNECT_EXCLUDED_CHATS_FILE`
  - optional path to a local JSON file with an `excluded_chats` array
  - default path: `./.penguin_connect_excluded_chats.json`
  - see `./excluded_chats.example.json` for the file format
  - matching entries are still visible in local state, but PenguinConnect skips discovery/import alias provisioning, conversation sync, and manual sends for them
  - use `./scripts/penguin_connect_excluded_chats.py` for an interactive browse-and-toggle workflow

## 1) Get Google OAuth Client JSON

You need a Desktop OAuth client JSON from Google Cloud.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select/create a project.
2. Enable Gmail API under `APIs & Services` -> `Library`.
3. Configure OAuth consent screen.
4. Create credentials:
  - `Create Credentials` -> `OAuth client ID`
  - Application type: `Desktop app`
5. Download the JSON.

Place the JSON in one of:

- `./client_secret.json`
- `./google_client_secret.json`
- `~/penguinconnect-local-bridge-data/google_client_secret.json`
- or set `PENGUIN_CONNECT_GOOGLE_CLIENT_SECRETS=/abs/path/to/client_secret.json`

## 2) Connect Gmail to Local Bridge

Start backend first:

```bash
cd /path/to/penguin-connect
./scripts/run_penguin_connect_bridge.sh
```

Normal startup now fails fast if Apple Messages access is missing or Gmail has not been connected yet. For first-time setup only, the guided setup flow starts the bridge with a temporary bootstrap override so you can complete Gmail OAuth.

Run Gmail connect helper:

```bash
./scripts/penguin_connect_connect.py --gmail you@gmail.com
```

This script:

- launches browser OAuth flow
- requests Gmail scopes including `https://mail.google.com/` (required for permanent delete APIs)
- produces `token_json` automatically from OAuth credentials
- calls `POST /penguin-connect/gmail/connect`
- stores token in macOS Keychain (device-only)

If you connected Gmail before this scope update, reconnect once to refresh saved scopes:

```bash
./scripts/penguin_connect_connect.py --gmail you@gmail.com
```

Verify:

```bash
curl -s http://127.0.0.1:9000/penguin-connect/gmail/status | jq
```

Example setup with custom footer removal:

```bash
./scripts/penguin_connect_setup.py \
  --gmail you@gmail.com \
  --signature-marker "External email:" \
  --signature-marker "Company Confidential"
```

If you skip the interactive exclusion prompt or want to revisit it later, run:

```bash
./scripts/penguin_connect_excluded_chats.py --gmail you@gmail.com
```

## 3) Verify and Catch Up

Run doctor:

```bash
./scripts/penguin_connect_doctor.py
```

Check local health endpoint:

```bash
curl -s http://127.0.0.1:9000/penguin-connect/health | jq
```

Run startup catch-up:

```bash
curl -s -X POST http://127.0.0.1:9000/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"startup_catchup"}' | jq
```

On server start, PenguinConnect also launches startup catch-up in the background and, by default, drains all pending bootstrap conversations in that run. Set `PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN` if you need to cap that startup batch.

Startup catch-up still imports full history for a conversation's first bootstrap. The recent-activity cutoff only prioritizes which pending conversations run first; it does not truncate the first-time bootstrap window.

First bootstrap only completes after the bridge either materializes Gmail history for that conversation or verifies that a full-history scan is empty. A zero-import startup pass no longer marks a conversation done by itself.

Incremental sync can keep running while startup catch-up or backfill is in progress. PenguinConnect serializes work within each lane and skips any conversation that is already being processed by the other lane, so the same conversation is never synced by both at once.

The incremental watcher and startup worker now lease only their own queued job mode, so a watcher poll cannot accidentally grab a long-running `startup_catchup` job and leave the real incremental work stranded in the queue.

If startup catch-up or backfill is actively importing iMessage history, PenguinConnect now checks for a queued incremental job after every 5 successful Gmail imports and yields early when fresh hot-work is waiting. Override that chunk size with `PENGUIN_CONNECT_STARTUP_INCREMENTAL_PREEMPTION_IMPORT_COUNT`.

When that yield happens during an initial Apple Messages bootstrap, the next startup/backfill pass resumes from the saved `(last_imessage_ts, last_imessage_native_message_id)` cursor instead of rescanning the conversation from the beginning.

Queue, selection, and per-message sync state are committed before PenguinConnect moves on to the next remote Gmail or Apple Messages call. That keeps the concurrent startup and watcher lanes from holding SQLite write locks across network waits or send retries.

When Gmail returns a rate limit cooldown, PenguinConnect now requeues the current sync job for the cooldown window instead of counting that pause as a failed sync attempt. Expect queued jobs with `last_error=gmail_rate_limited` during those windows.

Repeated Gmail rate limits now behave more like Slashy backend sync retries: PenguinConnect increases the cooldown window exponentially up to a cap and also slows startup/backfill Gmail writes before the next attempt. A successful sync that actually writes to Gmail resets that pressure; a successful no-write pass decays it gradually instead of dropping straight back to the most aggressive pace.

PenguinConnect now also applies a local per-account Gmail write budget before each bridge-owned Gmail write. Incremental work can spend the full shared budget, while startup/backfill must also fit inside a smaller backfill bucket. That keeps catch-up work from consuming all Gmail write capacity and leaves reserved headroom for fresh-message incremental sync.

Startup/backfill now also stop after a rolling 24-hour Gmail import cap, and they stand down for a longer cooldown when the Gmail rate-limit streak gets too high. Those guards are backfill-only: incremental sync keeps running so fresh-message delivery is not blocked by old-history catch-up.

PenguinConnect also cleans up stale Gmail drafts addressed to a conversation alias when they live in a non-canonical thread and the conversation already has a bridge-owned canonical thread. This prevents duplicate draft-only threads from lingering in Gmail while still leaving active in-progress drafts alone until they age past the safety window. The default safety window is 30 minutes and can be adjusted with `PENGUIN_CONNECT_ALIAS_DRAFT_DELETE_MINUTES`.

PenguinConnect also refreshes the local Contacts cache on startup and then again every 30 to 60 minutes while the watcher is running. That refresh pass repairs active conversation display names when a raw-handle group title such as `Sai Mandhan, +15126629638` can now resolve fully from contacts.

Once a conversation completes its first bootstrap, PenguinConnect schedules recurring randomized full verifications 3 to 8 days apart so “verify all” work is spread out instead of landing in one burst. On startup, PenguinConnect also repairs missing recurring verify schedules for already-bootstrapped conversations before sync selection runs.

Those recurring full verifications also refresh contact-derived display names, sender names, and subjects in local bridge state when your contacts have changed, without reimporting already-synced Gmail messages.

## Polling and Auto-Start

- default polling: `PENGUIN_CONNECT_POLL_SECONDS=30`
- incremental sync batch cap: `PENGUIN_CONNECT_INCREMENTAL_CONVERSATIONS_PER_RUN`
  - leave unset to let incremental runs expand to all currently hot conversations up to the built-in cap
- optional startup catch-up cap: `PENGUIN_CONNECT_STARTUP_CATCHUP_CONVERSATIONS_PER_RUN` (unset means all pending bootstrap conversations)
- startup/backfill incremental-yield import chunk: `PENGUIN_CONNECT_STARTUP_INCREMENTAL_PREEMPTION_IMPORT_COUNT=5`
- backfill Gmail write pacing base: `PENGUIN_CONNECT_BACKFILL_WRITE_PAUSE_SECONDS=0.15`
  - repeated Gmail throttles automatically scale that pause up to 5 seconds until sync recovers
- total Gmail write budget per minute: `PENGUIN_CONNECT_GMAIL_WRITE_BUDGET_UNITS_PER_MINUTE=3000`
- startup/backfill Gmail write budget per minute: `PENGUIN_CONNECT_GMAIL_BACKFILL_WRITE_BUDGET_UNITS_PER_MINUTE=1200`
- Gmail write budget cost per reserved write: `PENGUIN_CONNECT_GMAIL_WRITE_OPERATION_COST_UNITS=25`
- startup/backfill rolling 24-hour Gmail import cap: `PENGUIN_CONNECT_BACKFILL_DAILY_GMAIL_IMPORT_CAP=500`
- startup/backfill rate-limit guard streak: `PENGUIN_CONNECT_BACKFILL_RATE_LIMIT_GUARD_STREAK=8`
- startup/backfill rate-limit guard pause: `PENGUIN_CONNECT_BACKFILL_RATE_LIMIT_GUARD_PAUSE_SECONDS=3600`
- Gmail rate-limit cooldown base/max: `PENGUIN_CONNECT_GMAIL_RATE_LIMIT_PAUSE_SECONDS=120`, `PENGUIN_CONNECT_GMAIL_RATE_LIMIT_MAX_PAUSE_SECONDS=1800`
- action log:
  - `PENGUIN_CONNECT_ACTION_LOG_PATH`
  - `PENGUIN_CONNECT_ACTION_LOG_MAX_BYTES`
  - `PENGUIN_CONNECT_ACTION_LOG_BACKUPS`
- durable sync queue retries:
  - `PENGUIN_CONNECT_SYNC_JOB_MAX_ATTEMPTS=12`
  - `PENGUIN_CONNECT_SYNC_JOB_LEASE_SECONDS=180`
  - `PENGUIN_CONNECT_SYNC_JOB_RETRY_BASE_SECONDS=30`
  - `PENGUIN_CONNECT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS=1800`
- retry policy defaults:
  - `PENGUIN_CONNECT_RETRY_BASE_SECONDS=30`
  - `PENGUIN_CONNECT_RETRY_MAX_BACKOFF_SECONDS=900`
  - `PENGUIN_CONNECT_MAX_RETRIES=8`
  - `PENGUIN_CONNECT_GMAIL_TO_SOURCE_MAX_RETRIES=3`
- stale alias draft cleanup window:
  - `PENGUIN_CONNECT_ALIAS_DRAFT_DELETE_MINUTES=30`

Install login auto-start:

```bash
./scripts/install_launchd_penguin_connect_bridge.sh
```

That command installs a launchd watchdog that runs at login and every 5 minutes. It only starts the bridge when nothing is listening on the configured local port.

The watchdog is intentionally start-only. It never kills a running bridge, so Gmail rate-limit cooldowns or temporary health warnings do not trigger forced restarts.

If you later change `PENGUIN_CONNECT_PORT`, rerun the installer so the watchdog uses the updated port.

## Operational Commands

```bash
curl -s http://127.0.0.1:9000/penguin-connect/conversations | jq
curl -s http://127.0.0.1:9000/penguin-connect/conversations/<conversation_id>/alias | jq
curl -s -X POST http://127.0.0.1:9000/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"incremental"}' | jq
./scripts/penguin_connect_backfill.py --max-attempts 20
./scripts/penguin_connect_audit_quote_parsing.py --limit 100
./scripts/penguin_connect_excluded_chats.py
curl -s -X POST http://127.0.0.1:9000/penguin-connect/conversations/<conversation_id>/send \
  -H 'Content-Type: application/json' \
  -d '{"sender_email":"you@gmail.com","message":"hello"}' | jq
```

Operational note:
- `/api/status` and `/penguin-connect/health` return a cached sync-metrics snapshot so they stay responsive during large backfills and Gmail cooldowns.
- When `sync_metrics.snapshot_complete` is `false`, the durable queue and runtime state are current, while the detailed delivery counters are still refreshing in the background.

## Action Log

PenguinConnect writes operational events to a local JSONL action log for debugging and incident review.

- default path: `~/penguinconnect-local-bridge-data/actions.jsonl`
- stores identifiers, timestamps, statuses, and message fingerprints
- does not store raw message text

Use this when you need to answer whether the bridge sent, skipped, retried, or rejected a message.

Useful sync events:

- `sync_run_started` / `sync_run_result`
- `sync_conversation_started` / `sync_conversation_result`
- `gmail_alias_activity_scan_result`
- `gmail_pending_activity_recorded`
- `gmail_pending_activity_cleared`

The server also prints human-readable sync progress to stdout:

- run start summary with selected count and strategy
- per-conversation completion line showing imports, sends, repairs, draft cleanup, bootstrap/full-verify completion, or `result=no_changes`
- run completion summary with aggregate totals

## Quote Parsing Audit

To evaluate whether Gmail replies are being reduced to net-new content correctly:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100
```

Machine-readable output:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100 --json
```

Rewrite cached Gmail-to-chat bodies from the live Gmail message when the parser now does a better job:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100 --rewrite-db
```

## Troubleshooting

`gmail_not_connected`

- rerun `./scripts/penguin_connect_connect.py --gmail <you@gmail.com>`

`sender_not_connected_gmail`

- sender must be connected Gmail primary or verified send-as alias

`redirect_uri_mismatch` during Gmail connect

- ensure OAuth JSON is Desktop app type
- use `./client_secret.json` or pass `--client-secrets /abs/path/file.json`

`{"detail":"imessage_db_unreadable"}`

- grant Full Disk Access to Terminal.app
- rerun `./scripts/penguin_connect_doctor.py`
