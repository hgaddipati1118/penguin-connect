# PenguinConnect

PenguinConnect is a local email-to-messaging bridge. It lets a Gmail inbox act as the control surface for messaging conversations while keeping all messaging-side access on the local machine.

Current runtime is macOS-only because the first source adapter is Apple Messages.

## Current And Planned Channels

- Current source adapter: Apple Messages
- Apple Messages services supported now: `iMessage`, `SMS`, `RCS`
- Planned next adapters: WhatsApp, Telegram
- Shared inbox surface: Gmail threads plus per-conversation alias addresses
- Architecture direction: one bridge core with provider adapters under `server/channels/`

## Key Behavior

- Local runtime only on `127.0.0.1`
- `conversation_id` is the primary logical identity
  - derived from Gmail account + messaging platform + source chat identity
- Apple Messages direct messages are unified into one logical thread when multiple Apple routes exist for the same person
  - actual sends still use one exact backend chat route
  - when the route is ambiguous, the bridge fails closed and does not send
- Apple Messages group chats stay separate
- Group-chat email thread subjects use the group title when available
- One alias email per conversation
- Two-way sync:
  - Apple Messages -> Gmail inbox thread
  - Gmail replies to alias -> Apple Messages
- Gmail-to-Apple-Messages delivery only processes messages from Gmail `SENT` that still target the exact conversation alias; drafts are ignored
- Gmail-to-chat delivery strips quoted history and signatures aggressively and sends only net-new text
- Gmail-to-chat delivery retries failed sends up to 3 times; after the final failure the bridge posts a `PENGUIN_CONNECT` error reply into the Gmail thread with the failed message body
- Durable local sync queue (SQLite): queued and leased jobs survive process pauses and resume with retries
- Durable server action log: `~/penguinconnect-local-bridge-data/actions.jsonl`
- Startup gap fill plus default 7-day backfill
- Startup catch-up runs in a background thread and drains all pending bootstrap conversations by default
- Startup catch-up still imports full history for a conversation's first bootstrap; the recent-activity cutoff only decides which pending conversations run first
- Contacts refresh at startup and then again every 30 to 60 minutes, repairing raw-handle group titles when participant contacts resolve
- After initial bootstrap, each conversation gets a recurring randomized full-verify schedule 3 to 8 days apart so verify-all work stays spread out
- Full verify refreshes contact-derived conversation/message names in local state without reimporting already-synced messages
- Startup repairs any missing recurring full-verify schedule for already-bootstrapped conversations before selection runs
- Startup catch-up and backfill perform a full self-heal sweep across all Apple Messages chats
  - old cache rows are migrated into the current canonical thread format before sync continues
- Sender gate blocks non-connected Gmail senders (`403 sender_not_connected_gmail`)

## Prerequisites

- macOS 13+
- Terminal.app with Full Disk Access
  - Required for Apple Messages `chat.db` reads; run setup and server commands from Terminal.app
- Python 3.11+

## Install

```bash
cd /path/to/penguin-connect

cp .env.example .env

cd server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cd ..
```

## One-Command Guided Setup

Run the guided wizard. It explains each step and executes setup in order:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com
```

Important: run this from Terminal.app with Full Disk Access enabled.

For fully non-interactive defaults:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com --yes
```

Skip the final sync smoke if needed:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com --skip-sync-smoke
```

If OAuth fails with `redirect_uri_mismatch`, the setup scripts print the exact Google Cloud fix steps for a Desktop OAuth client.

## Required Setup Order

1. Create a Google OAuth Desktop client JSON.
2. Connect Gmail.
3. Run doctor and the startup sync.

The current setup flow is Apple-Messages-first because Apple Messages is the only implemented source adapter today.

Full setup and operations guide: [docs/PENGUIN_CONNECT.md](./docs/PENGUIN_CONNECT.md)

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

Before running for users, run this from Terminal.app with Full Disk Access:

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
  -d '{"mode":"backfill","verify_all":true}' | jq
```

If sync returns `{"detail":"imessage_db_unreadable"}`, Terminal does not have Full Disk Access yet.

## Action Log

Every significant bridge action can be written to a durable JSONL file:

- default path: `~/penguinconnect-local-bridge-data/actions.jsonl`
- override path: `PENGUIN_CONNECT_ACTION_LOG_PATH`
- rotation controls:
  - `PENGUIN_CONNECT_ACTION_LOG_MAX_BYTES`
  - `PENGUIN_CONNECT_ACTION_LOG_BACKUPS`

The log is intended for operational debugging. It stores identifiers, statuses, timestamps, and message fingerprints. It does not store raw message text.

## Quote Parsing Audit

To evaluate how well Gmail replies are reduced to net-new text, audit recent Gmail-to-chat deliveries against the current parser:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100
```

JSON output:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100 --json
```

Rewrite the local cache from current Gmail message bodies when the parser now produces cleaner text:

```bash
./scripts/penguin_connect_audit_quote_parsing.py --limit 100 --rewrite-db
```

This checks the stored Gmail-to-chat body against the text that would be produced now by the shared quote parser.

## Useful Scripts

- `./scripts/penguin_connect_setup.py --gmail you@gmail.com`
- `./scripts/penguin_connect_connect.py --gmail you@gmail.com`
- `./scripts/penguin_connect_doctor.py`
- `./scripts/penguin_connect_backfill.py --max-attempts 20`
- `./scripts/penguin_connect_audit_quote_parsing.py --limit 100`
- `./scripts/penguin_connect_verify_contact_resolution.py --handle +15127436385 --all-active --limit 100`
- `./scripts/import_contacts.py`
- `./scripts/install_launchd_penguin_connect_bridge.sh`
- `./scripts/check.sh`

## Project Layout

```text
server/            FastAPI app, local DB, sync engine, quote parser, action log
server/channels/   messaging-provider adapters (Apple Messages today)
scripts/           setup, OAuth connect, doctor, launchd, audit, operations
docs/              setup and operational documentation
skills/            repo-local agent guidance for commits and channel integrations
```

## Community And Contributor Docs

- Contributor guide: [CONTRIBUTING.md](./CONTRIBUTING.md)
- Security policy: [SECURITY.md](./SECURITY.md)
- Agent onboarding: [AGENTS.md](./AGENTS.md)
- Code of conduct: [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
- License: [LICENSE](./LICENSE)

PenguinConnect is released under the MIT License.
