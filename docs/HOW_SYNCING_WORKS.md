# How PenguinConnect Handles Syncing, Backfill, and Rate Limits

PenguinConnect has to move messages between Apple Messages and Gmail without hammering Gmail's API, losing your place on restart, or letting old history import block real-time conversations. Here's how it works.

## Sync Lanes

All sync work runs in two lanes:

- **Incremental lane**: Handles real-time message syncing for currently active conversations. This always gets priority.
- **Backfill lane**: Handles startup catch-up and historical imports. This runs in the background and yields to incremental work.

Each lane has its own lock, so incremental and backfill can run concurrently — but two backfill jobs can't run at the same time.

## Incremental Sync

The watcher polls every 30 seconds (configurable 10–300s). Each poll enqueues an incremental sync job that:

1. **Finds hot conversations** — conversations with iMessage activity in the last ~13 minutes or pending Gmail replies
2. **Expands to fit** — if 8 conversations are hot, it syncs all 8 (up to a cap of 20). If nothing is hot, it round-robins through one conversation per poll
3. **Fetches new messages** from `chat.db` using the saved cursor (timestamp + native message ID)
4. **Imports to Gmail** and updates the cursor after each message

Because incremental runs every 30 seconds and only touches active conversations, most messages appear in Gmail within a minute.

## Startup Catch-Up

When the bridge starts and discovers conversations that haven't been initially synced yet, it queues startup catch-up jobs. These run on the backfill lane in small batches (5 conversations per run by default) so they don't starve incremental sync.

Startup catch-up processes conversations sorted by recent activity — the most active conversations get synced first.

## Backfill

Historical message import uses a wave-based approach:

- Backfill looks back over a configurable time window (default 7 days)
- Messages are fetched in batches of 500 from `chat.db`
- A **rolling 24-hour cap of 50 Gmail imports** prevents backfill from consuming your entire Gmail API quota. Once 50 imports are done, backfill pauses until the 24-hour window expires
- Each conversation's progress is saved as a cursor (timestamp + message ID), so if the bridge restarts, backfill picks up exactly where it left off instead of rescanning from the beginning

## Yielding to Incremental

Backfill and startup catch-up yield to incremental work at two points:

1. **Between conversations**: After each conversation finishes syncing, the backfill loop commits and briefly sleeps (0.1s) to give the incremental lane a chance to acquire the lock
2. **Mid-conversation**: After every 5 Gmail imports within a single conversation, the sync checks if an incremental job is waiting. If one is, it immediately stops importing, saves the cursor, and returns — the conversation will resume from that exact message on the next backfill run

This means a large backfill importing thousands of messages will never block an incoming text from syncing to Gmail for more than a few seconds.

## Gmail Rate Limiting

PenguinConnect manages Gmail API usage through three mechanisms:

### Per-Write Budget

A token bucket system controls Gmail write throughput:

- **Total budget**: 3000 units/minute across all sync lanes
- **Backfill budget**: 1200 units/minute (a subset of total)
- **Incremental headroom**: The remaining 1800 units/minute is reserved for incremental work

If a lane exhausts its budget, it waits for tokens to refill before the next write.

### Exponential Backoff

When Gmail returns a 429 (rate limit) error, PenguinConnect tracks it as a streak:

- Each rate limit increments the streak counter
- Each successful write decrements it by 1
- The pause between backfill writes scales exponentially: `0.15s × 2^streak`, capped at 5 seconds

So a single rate limit adds a slight slowdown, but sustained rate limiting causes backfill to back off aggressively while incremental keeps its reserved headroom.

| Streak | Pause Between Writes |
|--------|---------------------|
| 0      | 0.15s               |
| 1      | 0.30s               |
| 2      | 0.60s               |
| 3      | 1.20s               |
| 5      | 4.80s               |
| 6+     | 5.00s (max)         |

### Rate Limit Guard

If the streak hits 8, backfill stands down entirely for an hour. This prevents old-history catch-up from repeatedly hammering a throttled account while incremental work waits.

## Cursor Recovery

Every message import updates two cursor values in the database:

- `last_imessage_ts`: The timestamp of the last successfully imported message
- `last_imessage_native_message_id`: A tiebreaker for messages with identical timestamps

On the next sync — whether it's a normal poll, a resume after preemption, or a restart after a crash — the query uses these cursors to fetch only messages newer than the last import. No rescanning, no duplicates.

When two cursors conflict (e.g., from concurrent lanes), the merge logic keeps the later timestamp and uses the native message ID as a tiebreaker for equal timestamps.

## The Watcher

The background watcher ties everything together:

- **Polling thread**: Runs every 30 seconds, triggers incremental sync, and periodically refreshes contacts (every 30–60 minutes)
- **Watchdog thread**: Monitors the polling thread every 10–60 seconds. If the poller hasn't produced a result in 6+ minutes, the watchdog restarts it with a new generation token (the old thread exits gracefully)
- **Health endpoint**: `/penguin-connect/health` reports watcher status, last poll times, sync stats, and rate limit state — so you can see at a glance if the bridge is healthy

The result: real-time messages sync in under a minute, historical backfill happens in the background without disruption, and Gmail rate limits are respected automatically.
