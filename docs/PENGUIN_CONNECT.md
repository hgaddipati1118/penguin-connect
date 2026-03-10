# PenguinConnect (Mac Local Bridge)

PenguinConnect bridges messaging conversations to a user's Gmail inbox using per-conversation alias addresses.

Current implemented source adapter: iMessage.

Planned next source adapters: WhatsApp and Telegram.

This bridge is macOS local-only and runs on `127.0.0.1`.

## Fast Path: Guided Setup CLI

```bash
cd /path/to/penguinconnect
./scripts/penguin_connect_setup.py --gmail you@gmail.com
```

Optional flags:

- `--yes` for default-yes non-interactive execution
- `--client-secrets /abs/path/client_secret.json` to force OAuth JSON path
- `--skip-sync-smoke` to skip final sync endpoint smoke test
- `--explain-only` to print steps without executing

## Setup Order (Required Sequence)

1. Get Google OAuth client JSON.
2. Connect Gmail to local bridge (requests full Gmail mailbox access scope).
3. Run startup catch-up and verify health.

## What the Bridge Does

- Creates deterministic `conversation_id` values from Gmail account + source provider + source chat id.
- Assigns one active alias email per conversation.
- Imports iMessage messages into Gmail inbox threads.
- Polls Gmail for replies to alias addresses and sends those replies back to the source provider.
- Applies sender gate:
  - connected Gmail primary address, or
  - verified Gmail send-as alias for same inbox.

## Prerequisites

- macOS 13+
- Terminal.app with Full Disk Access
- Backend running locally on `127.0.0.1:8888`
- Python deps installed in `server/venv`

Important:

- run setup and bridge commands from `Terminal.app`
- Full Disk Access is required to read `~/Library/Messages/chat.db`
- macOS path: `System Settings -> Privacy & Security -> Full Disk Access -> Terminal`

Install backend deps if needed:

```bash
cd /path/to/penguinconnect/server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

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
- `~/penguinconnect-data/google_client_secret.json`
- or set `PENGUIN_CONNECT_GOOGLE_CLIENT_SECRETS=/abs/path/to/client_secret.json`

## 2) Connect Gmail to Local Bridge

Start backend first:

```bash
cd /path/to/penguinconnect
./scripts/run_penguin_connect_bridge.sh
```

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
curl -s http://127.0.0.1:8888/penguin-connect/gmail/status | jq
```

## 3) Verify and Catch Up

Run doctor:

```bash
./scripts/penguin_connect_doctor.py
```

Check local health endpoint:

```bash
curl -s http://127.0.0.1:8888/penguin-connect/health | jq
```

Run startup catch-up:

```bash
curl -s -X POST http://127.0.0.1:8888/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"startup_catchup"}' | jq
```

## Polling and Auto-Start

- default polling: `PENGUIN_CONNECT_POLL_SECONDS=30`
- backfill Gmail write pacing: `PENGUIN_CONNECT_BACKFILL_WRITE_PAUSE_SECONDS=0.15`
- durable sync queue retries:
  - `PENGUIN_CONNECT_SYNC_JOB_MAX_ATTEMPTS=12`
  - `PENGUIN_CONNECT_SYNC_JOB_LEASE_SECONDS=180`
  - `PENGUIN_CONNECT_SYNC_JOB_RETRY_BASE_SECONDS=30`
  - `PENGUIN_CONNECT_SYNC_JOB_RETRY_MAX_BACKOFF_SECONDS=1800`
- retry policy defaults:
  - `PENGUIN_CONNECT_RETRY_BASE_SECONDS=30`
  - `PENGUIN_CONNECT_RETRY_MAX_BACKOFF_SECONDS=900`
  - `PENGUIN_CONNECT_MAX_RETRIES=8`

Install login auto-start:

```bash
./scripts/install_launchd_penguin_connect_bridge.sh
```

## Operational Commands

```bash
curl -s http://127.0.0.1:8888/penguin-connect/conversations | jq
curl -s http://127.0.0.1:8888/penguin-connect/conversations/<conversation_id>/alias | jq
curl -s -X POST http://127.0.0.1:8888/penguin-connect/conversations/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"incremental"}' | jq
./scripts/penguin_connect_backfill.py --max-attempts 20
curl -s -X POST http://127.0.0.1:8888/penguin-connect/conversations/<conversation_id>/send \
  -H 'Content-Type: application/json' \
  -d '{"sender_email":"you@gmail.com","message":"hello"}' | jq
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
