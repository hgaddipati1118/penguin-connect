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
- Apple Messages read state is mirrored back to Gmail `UNREAD` labels at the conversation level
  - the latest synced inbound Apple Messages stay unread in Gmail until the conversation is read in Messages
- Gmail-to-Apple-Messages delivery only processes messages from Gmail `SENT` that still target the exact conversation alias; drafts are ignored
- Incremental Gmail reply detection persists per-conversation pending sent activity until that conversation is actually synced, so one poll cannot silently drop an unsent alias reply behind the global mailbox cursor
- Gmail-to-chat delivery strips quoted history and signatures aggressively and sends only net-new text
  - parsing is HTML-first and DOM-aware for Gmail/Slashy-style quote blocks, wrapped `On ... wrote:` headers, and common client quote containers
- When PenguinConnect rejects a real Gmail reply because the parsed body is still ambiguous, it posts a rejection notice back into the Gmail thread instead of failing silently
- Gmail-to-chat delivery retries failed sends up to 3 times; after the final failure the bridge posts a `PENGUIN_CONNECT` error reply into the Gmail thread with the failed message body
- Durable local sync queue (SQLite): queued and leased jobs survive process pauses and resume with retries
- Durable server action log: `~/penguinconnect-local-bridge-data/actions.jsonl`
- Startup gap fill plus default 7-day backfill
- Startup catch-up runs in a background thread and drains all pending bootstrap conversations by default
- Startup catch-up still imports full history for a conversation's first bootstrap; the recent-activity cutoff only decides which pending conversations run first
- First bootstrap now only counts as complete after Gmail thread materialization or a full-history empty verification, so conversations are not marked synced after a zero-import pass
- Incremental sync can keep running while startup catch-up or backfill is in progress; PenguinConnect only blocks the same conversation from being synced by both lanes at once
- Incremental and startup workers lease only their own queued mode, so watcher polls cannot accidentally steal long-running startup jobs and starve hot conversations
- Queue, selection, and per-message sync state are committed before the next remote Gmail/iMessage call, so concurrent startup and watcher lanes do not hold SQLite write locks across network waits
- Stale alias-only Gmail drafts in non-canonical threads are deleted automatically after a safety window so duplicate draft threads do not accumulate
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

Add custom email signature/disclaimer cutoff markers during setup if you have recurring footer text the parser should always remove:

```bash
./scripts/penguin_connect_setup.py \
  --gmail you@gmail.com \
  --signature-marker "External email:" \
  --signature-marker "Company Confidential"
```

Those markers are written to the local JSON preferences file at `./.penguin_connect_signature_markers.json` by default.

Skip the final sync smoke if needed:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com --skip-sync-smoke
```

If OAuth fails with `redirect_uri_mismatch`, the setup scripts print the exact Google Cloud fix steps for a Desktop OAuth client.

Custom parsing option:

- `PENGUIN_CONNECT_SIGNATURE_MARKERS_FILE`
  - optional path to a local JSON file with `signature_markers`
  - default path: [`.penguin_connect_signature_markers.json`](/Users/harshagaddipati/Documents/penguin-connect/.penguin_connect_signature_markers.json)
  - example format: [`signature_markers.example.json`](/Users/harshagaddipati/Documents/penguin-connect/signature_markers.example.json)
  - when a normalized line starts with one of those markers, PenguinConnect strips that line and everything after it before Gmail-to-chat send

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

Useful sync events include:

- `sync_run_started` / `sync_run_result`
- `sync_conversation_started` / `sync_conversation_result`
- `gmail_alias_activity_scan_result`
- `gmail_pending_activity_recorded`
- `gmail_pending_activity_cleared`

The bridge also prints terminal progress summaries for each sync run plus a per-conversation result line so an operator can watch startup catch-up, backfill, and incremental work live without tailing JSON.

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
