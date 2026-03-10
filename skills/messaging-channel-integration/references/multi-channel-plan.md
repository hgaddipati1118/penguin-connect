# Multi-Channel Integration Plan

Use this file when designing the refactor from one source provider to many.

## iMessage-specific pressure points

- Schema:
- `penguin_connect_conversations.source_provider`
- `penguin_connect_conversations.imessage_chat_id`
- legacy chat-id column name still exists even though new conversation ids are provider-aware
- sync-state column names such as `last_imessage_ts`

- Direction and status naming:
- `imessage_to_email`
- `email_to_imessage`
- `manual_to_imessage`
- `imessage_db_unreadable`

- Provider-specific helpers in the bridge core:
- `browse_imessage_chats`
- `list_recent_imessage_chat_activity`
- `fetch_imessage_messages`
- `send_imessage`
- `_resolve_imessage_sender_and_subject`
- `_get_imessage_unread_count`

- Product text and metadata:
- subject prefix `iMessage · ...`
- metadata keys like `imessage_chat_id`
- setup and doctor guidance tied to `chat.db`, AppleScript, Terminal.app, and Full Disk Access

## Recommended abstractions before provider two

1. Extract a provider adapter layer.

- `server/channels/base.py` and `server/channels/imessage.py` now exist.
- Continue moving provider discovery, fetch, send, unread-state, and display-name logic behind that adapter boundary instead of back into `server/penguin_connect.py`.

2. Define normalized provider contracts.

- `ChannelConversation`
- provider key
- source chat id
- display name
- chat type
- participants

- `ChannelMessage`
- provider message id
- timestamp
- text
- attachments
- is-from-me flag
- sender handle
- sender label
- unread hint

- `ChannelAdapter`
- list conversations
- list recent activity
- fetch messages since cursor
- send outbound message
- resolve display metadata
- expose provider health requirements

3. Generalize the schema.

- Replace `imessage_chat_id` with generic source identity columns such as `source_provider` and `source_chat_id`.
- Revisit direction enums so they do not freeze iMessage into the storage model.
- Keep Gmail-specific columns only where Gmail truly remains the shared inbox surface.
- Move new provider-specific fields into explicit columns or strongly versioned metadata instead of ad hoc growth.

4. Keep shared bridge logic shared.

- Alias generation and routing
- RFC threading and canonical Gmail thread repair
- Gmail sender gate
- durable queueing and retry backoff
- sync selection framework
- metrics and health payloads

5. Keep provider-specific logic isolated.

- auth and session storage
- local or remote history access model
- polling or webhook intake
- attachment download rules
- outbound send API
- provider-specific health checks and setup scripts

## Suggested implementation order

1. Extract the current iMessage code into an adapter without changing behavior.
2. Migrate schema and naming to provider-neutral fields.
3. Route sync orchestration through the adapter boundary.
4. Update scripts, doctor checks, and docs so provider requirements are explicit instead of implicit.
5. Add the second provider only after the first four steps pass with the existing iMessage test suite.

## New-provider checklist

- Define the capability matrix:
- discovery
- history access
- incremental update source
- outbound send
- attachment support
- read-state signal
- auth or device requirements

- Define the identity model:
- stable chat id
- stable message id
- participant identity shape
- mapping into `conversation_id`

- Define the bridge behavior:
- source-to-Gmail import
- Gmail-to-source reply handling
- quoting and reply threading
- blocked or ignored cases
- retry and rate-limit behavior

- Define the operational surface:
- connect or auth script
- doctor checks
- setup steps
- docs and troubleshooting

- Define the tests:
- discovery
- sync selection
- first backfill
- incremental loop
- delivery retries
- attachments
- threading and repair
- disconnect and reconnect
- metrics and health

## Anti-patterns

- Do not keep adding provider-specific branches directly inside `server/penguin_connect.py`.
- Do not treat `imessage_chat_id` as the only source identifier once multiple providers exist.
- Do not skip backfill, retry, or thread-repair behavior for a new provider just because the happy path works.
- Do not merge provider-specific setup constraints into generic docs without scoping them.
- Do not claim provider parity until discovery, sync, send, attachments, and health checks all exist.
