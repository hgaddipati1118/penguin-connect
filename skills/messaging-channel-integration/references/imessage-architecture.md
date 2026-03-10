# iMessage Architecture Map

Use this file to understand what PenguinConnect already does for iMessage before generalizing it to WhatsApp, Telegram, or another provider.

## End-to-end flow

1. Discover source conversations from the provider.
2. Derive a stable `conversation_id` from account identity plus provider chat identity.
3. Ensure one active Gmail alias per active conversation.
4. Mirror source messages into Gmail with RFC threading metadata and attachment forwarding.
5. Detect inbound Gmail replies to the alias and deliver them back to the source provider.
6. Persist message, sync, poll, and queue state in SQLite so retries survive process restarts.
7. Expose the system through FastAPI, background polling, and setup or doctor scripts.

## Current layer map

### Source adapter behavior

- `server/channels/imessage.py`
- `IMessageChannelAdapter`: current provider adapter boundary for iMessage-specific discovery, send, unread-count, and sender-label logic

- `server/browse_sources.py`
- `browse_imessage_chats`: discover iMessage chats and participants from `chat.db`
- `list_recent_imessage_chat_activity`: drive incremental and backfill selection from recent source activity
- `fetch_imessage_messages`: load text and supported attachments from the source history

### Gmail account and sender trust

- `connect_gmail_account`: validate OAuth payload, store it in macOS Keychain, and cache account state in SQLite
- `_build_gmail_service`: hydrate Gmail API client from Keychain and refresh credentials when needed
- `_refresh_send_as_aliases`: sync verified Gmail send-as addresses
- `_sender_allowed`: enforce the sender gate for Gmail-to-provider sends and manual sends

### Conversation discovery and identity

- `deterministic_conversation_id`: derive a stable conversation identifier from normalized Gmail address, provider key, and provider chat id
- `_create_alias_email`: derive deterministic or fresh alias local parts from the conversation id
- `_ensure_active_alias`: enforce one active alias row per active conversation
- `ensure_conversations_discovered`: pull source conversations into `penguin_connect_conversations` and attach aliases

### Shared bridge logic

- `_select_conversations_for_sync`: choose which conversations to process in incremental, startup, backfill, and verify-all modes
- `_sync_conversation_imessage_to_gmail`: source-to-Gmail import path
- `_sync_conversation_gmail_to_imessage`: Gmail-to-source reply path
- `_retry_pending_imessage_to_gmail` and `_retry_pending_gmail_to_imessage`: durable delivery recovery
- `_build_import_email`: generate Gmail-importable MIME with RFC threading headers
- `_import_message_to_gmail_with_thread_recovery`: import into Gmail and recover when thread ids are stale
- `_repair_split_gmail_messages`: reconcile split Gmail threads back to one canonical thread
- `_compose_imessage_delivery_body`: add quoted reply context when sending Gmail replies back to the source
- `_upsert_sync_state`: persist monotonic sync cursors
- `enqueue_sync_job` and `run_sync_job_worker_once`: durable queued sync execution
- `sync_conversations`: single-flight orchestration over all selected conversations

### API and runtime surfaces

- `server/app.py`: health, Gmail connect, conversation list, alias lookup, sync, disconnect, reconnect, and manual send endpoints
- `server/watcher.py`: background incremental polling loop with Gmail rate-limit and bootstrap gating
- `scripts/penguin_connect_setup.py`: guided setup flow
- `scripts/penguin_connect_connect.py`: Gmail OAuth helper
- `scripts/penguin_connect_doctor.py`: readiness and environment checks
- `scripts/penguin_connect_backfill.py`: controlled backfill with rate-limit waiting

## Current data model

- `contacts`: display-name resolution cache
- `penguin_connect_accounts`: connected Gmail account and verified send-as addresses
- `penguin_connect_conversations`: one row per active or disconnected source conversation, now tagged with `source_provider`
- `penguin_connect_aliases`: alias lifecycle with one active alias per conversation
- `penguin_connect_messages`: deduped source and Gmail messages plus delivery metadata
- `penguin_connect_sync_state`: per-conversation source and Gmail cursors plus bootstrap marker
- `penguin_connect_poll_state`: mailbox history cursor and rate-limit pause window
- `penguin_connect_jobs`: durable sync queue with dedupe, lease, retry, and result state

## Invariants future providers must preserve

- `conversation_id` is the canonical join key across tables and API routes.
- One active alias maps to one active conversation.
- The sender gate blocks Gmail replies from untrusted senders.
- Bridge-generated Gmail messages are ignored on the Gmail-to-source path.
- Gmail thread ownership is preserved with RFC `Message-ID`, `In-Reply-To`, and `References` headers.
- Initial backfill completes before steady-state incremental polling takes over.
- Rate limits, retries, and durable jobs survive restarts and partial failures.

## Current test map

- Identity and sync selection:
- deterministic conversation ids
- startup and incremental selection strategy
- bootstrap gating

- Delivery and retry:
- source-to-Gmail retry queue
- Gmail-to-source retry queue
- retry backoff limits
- durable sync job dedupe and worker retries

- Threading and repair:
- nested reply headers
- parent-thread recovery
- canonical thread preference
- split-thread repair

- Attachments and rendering:
- iMessage attachment import into Gmail
- Gmail attachment download and forward to source
- placeholder bodies for attachment-only Gmail messages
- quoted reply context for Gmail replies

- Operations and runtime:
- disconnect and reconnect lifecycle
- health and status payloads
- single-flight sync behavior
- rate-limit handling
- `imessage_db_unreadable` mapping
