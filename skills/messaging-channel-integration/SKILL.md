---
name: messaging-channel-integration
description: Blueprint for extending PenguinConnect beyond the current Apple Messages adapter by reusing and generalizing the bridge architecture. Use when Codex is planning or implementing WhatsApp, Telegram, or other messaging-channel integrations; extracting provider adapters; changing schema for multi-provider support; or mapping tests, sync flows, and ops scripts from the existing Apple Messages implementation onto a new provider.
---

# Messaging Channel Integration

Use this skill to treat the current Apple Messages bridge as the source of truth for future channel integrations without copy-pasting more provider-specific logic into the monolith.

Read `references/imessage-architecture.md` first for the current system map.

Read `references/multi-channel-plan.md` when designing WhatsApp, Telegram, or any second provider.

## Working Rules

- Preserve core bridge contracts: `conversation_id` remains the primary identity, one active alias remains attached to one active conversation, and Gmail thread continuity, sender gate, retry semantics, and sync metrics remain explicit.
- Do not add a second provider by copy-pasting more iMessage-style branches into `server/penguin_connect.py`.
- Extract provider adapters and keep Gmail bridge logic shared where possible.
- Keep provider capabilities explicit: conversation discovery, recent-activity detection, history fetch/backfill, outbound send, attachment handling, sender and display-name normalization, unread or read-state hints, and provider auth or session storage.
- Keep the quoted-content parser shared: delivery to chat should send only net-new text, and provider adapters should not invent their own reply-chain formatting.

## Workflow

1. Map the current Apple Messages implementation.
- Use `references/imessage-architecture.md` to locate the current discovery, send, sync, API, script, schema, and test surfaces.

2. Identify generalization pressure points before writing new provider code.
- Start with Apple-Messages-specific schema fields, direction names, metadata keys, docs, and setup assumptions.
- Use `references/multi-channel-plan.md` to plan migrations and abstraction boundaries.

3. Design the new provider as an adapter, not a fork.
- Implement equivalents for conversation discovery, recent activity, message fetch, outbound send, attachment staging, and sender-label resolution.
- Keep Gmail alias routing, RFC threading, durable queueing, rate-limit handling, and metrics shared when possible.
- Preserve the current fail-closed rule: do not send when route resolution is ambiguous or unresolved.

4. Preserve idempotency and directionality.
- Generate stable `provider_message_id` values for the new provider.
- Support both source-to-Gmail and Gmail-to-source directions, plus manual sends if the product still needs them.
- Record blocked, ignored, delivered, pending, and retrying states as first-class behavior.

5. Match the existing test bar.
- Port the current test categories, not just the happy path.
- Cover discovery, initial backfill, incremental hot selection, retry behavior, attachments, nested replies, split-thread repair, sender gate, disconnect/reconnect, and runtime metrics.

6. Report the design clearly.
- Return a capability matrix, schema changes, adapter interface, rollout order, and verification plan before or alongside implementation.

## Deliverables

- Include a provider capability matrix, schema and migration plan, adapter API or module boundary, sync-flow changes, auth/setup/doctor changes, and a test plus rollout sequence.

## Collaboration

- Use `open-source-standards` alongside this skill when committing or reviewing the resulting changes.
