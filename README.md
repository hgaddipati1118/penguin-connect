# PenguinConnect

PenguinConnect is a local email-to-messaging bridge. It lets users manage messaging conversations through Gmail threads, with iMessage implemented today and WhatsApp or Telegram planned next.

Current runtime is still macOS-only because the first source adapter is iMessage.

## Current And Planned Channels

- Current source adapter: iMessage
- Planned next adapters: WhatsApp, Telegram
- Shared inbox surface: Gmail threads plus per-conversation alias addresses
- Architecture direction: one bridge core with provider adapters under `server/channels/`

## Key Behavior

- Local runtime only (`127.0.0.1`)
- `conversation_id` is the primary identity
  - derived from Gmail account + messaging platform + source chat id
- 1 alias email per conversation
- Two-way sync:
  - iMessage -> Gmail inbox thread
  - Gmail replies to alias -> iMessage
- Provider seam exists for future source adapters
- Polling every 30 seconds by default
- Durable local sync queue (SQLite): queued/leased jobs survive process pauses and resume with retries
- Retry backoff + capped retries for failed deliveries
- Startup gap fill + default 7-day backfill
- Per-conversation destructive disconnect/reconnect
- Sender gate blocks non-connected Gmail senders (`403 sender_not_connected_gmail`)

## Prerequisites

- macOS 13+
- Terminal.app with Full Disk Access
  - Required for iMessage `chat.db` reads; run setup/server commands from Terminal.app
- Python 3.11+

## Install

```bash
cd /path/to/penguinconnect-mac-local-bridge

cp .env.example .env

cd server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cd ..
```

## One-Command Guided Setup (Recommended)

Run the guided wizard. It explains each step and executes setup in order:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com
```

Important: run this from Terminal.app with Full Disk Access enabled.

For fully non-interactive defaults:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com --yes
```

Skip final sync smoke if needed:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com --skip-sync-smoke
```

If OAuth fails with `redirect_uri_mismatch`, the setup scripts now print exact Google Cloud fix steps (including Desktop OAuth client setup and optional browser-agent-assisted Console setup).

## Required Setup Order

1. Get Google OAuth Desktop client JSON.
2. Connect Gmail (script auto-generates `token_json` and requests full Gmail scope, including `https://mail.google.com/`).
3. Run doctor + startup sync.

The current setup flow is iMessage-specific because that is the only implemented source adapter right now.

Full click-by-click instructions: [`docs/PENGUIN_CONNECT.md`](./docs/PENGUIN_CONNECT.md)

## Scope Upgrade / Reconnect

If Gmail was already connected before this scope update, reconnect once so the saved token includes full Gmail mailbox access (required for permanent delete APIs):

```bash
./scripts/penguin_connect_connect.py --gmail you@gmail.com
```

## Start Service

```bash
./scripts/run_penguin_connect_bridge.sh
```

or:

```bash
./start.sh
```

Health check:

```bash
curl -s http://127.0.0.1:8888/penguin-connect/health | jq
```

## Production Preflight

Before running for users, run this from `Terminal.app` (with Full Disk Access):

```bash
./scripts/check.sh
./scripts/penguin_connect_doctor.py

curl -sS http://127.0.0.1:8888/penguin-connect/health | jq
curl -sS http://127.0.0.1:8888/penguin-connect/conversations | jq '.connected, (.conversations | length)'
curl -sS -X POST http://127.0.0.1:8888/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"incremental","days":7}' | jq

curl -sS -X POST http://127.0.0.1:8888/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"backfill","hours":5}' | jq

curl -sS -X POST http://127.0.0.1:8888/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"backfill","verify_all":true}' | jq
```

`backfill` now scopes itself to conversations with iMessage activity inside the requested window and processes them from the earliest activity to the latest. Gmail OAuth only needs to be connected once up front unless requested scopes change.

Use `{"mode":"backfill","verify_all":true}` for a manual full verification pass. That rescans every active conversation and verifies the bridge has imported every iMessage and Gmail message it can dedupe, without relying on the recent-activity window.

For a controlled full backfill that automatically waits/retries when Gmail rate limits are hit:

```bash
./scripts/penguin_connect_backfill.py --max-attempts 20
```

You can tune pacing with:

```bash
PENGUIN_CONNECT_BACKFILL_WRITE_PAUSE_SECONDS=0.15
```

Durable queue settings (optional):

```bash
PENGUIN_CONNECT_SYNC_JOB_MAX_ATTEMPTS=12
PENGUIN_CONNECT_SYNC_JOB_LEASE_SECONDS=180
PENGUIN_CONNECT_SYNC_JOB_RETRY_BASE_SECONDS=30
PENGUIN_CONNECT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS=1800
```

If sync returns `{"detail":"imessage_db_unreadable"}`, Terminal does not have Full Disk Access yet.

## Useful Scripts

- `./scripts/penguin_connect_setup.py --gmail you@gmail.com`
- `./scripts/penguin_connect_connect.py --gmail you@gmail.com`
- `./scripts/penguin_connect_doctor.py`
- `./scripts/penguin_connect_backfill.py --max-attempts 20`
- `./scripts/penguin_connect_verify_contact_resolution.py --handle +15127436385 --all-active --limit 100`
- `./scripts/import_contacts.py`
- `./scripts/install_launchd_penguin_connect_bridge.sh`
- `./scripts/check.sh`

## Project Layout

```text
server/   FastAPI app + local DB + bridge engine
server/channels/  messaging-provider adapters (iMessage today)
scripts/  setup, OAuth connect, doctor, launchd, operations
docs/     setup and operational documentation
```
