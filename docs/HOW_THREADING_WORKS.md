# How PenguinConnect Converts Messages into Gmail Threads

Every Apple Messages conversation — whether it's an iMessage DM, an SMS thread, or an RCS group chat — becomes a single Gmail thread. Each conversation gets its own alias email address (like `you+am-Xk9mL2a1@gmail.com`), and every message in that conversation flows through that alias into one continuous thread.

## Conversation Discovery

PenguinConnect reads your local `chat.db` (the Apple Messages SQLite database on your Mac) and discovers conversations. Each one gets a deterministic `conversation_id` — a SHA-256 hash of your Gmail address, the messaging provider (iMessage/SMS/RCS), and the chat identifier. Same inputs always produce the same ID, so conversations are stable across restarts.

## Alias Email Generation

Each conversation gets a unique plus-address alias on your Gmail account:

```
you+am-{conversation_hash}-{random_suffix}@gmail.com
```

This alias is what makes threading work — Gmail groups all emails to/from the same alias into one thread. When you reply to a conversation from Gmail, your reply goes to this alias, and PenguinConnect knows exactly which Apple Messages conversation it belongs to.

## Message to Email Conversion

Each iMessage/SMS/RCS message becomes a properly formatted RFC 822 email:

- **From**: The sender's name, with the conversation alias as the email address
- **To**: Your primary Gmail address
- **Subject**: The conversation name (e.g., "iMessage · Family Group")
- **Date**: The original message timestamp, preserved exactly
- **Custom headers**: `X-PenguinConnect-Conversation-ID` and `X-PenguinConnect-Source-Provider` for routing

## Threading via RFC Headers

This is the key mechanism. Gmail threads emails together based on three RFC headers:

- **Message-ID**: A deterministic hash of the conversation ID + message ID, formatted as `<am.{sha1}@penguinconnect.local>`. Same message always gets the same ID.
- **In-Reply-To**: Points to the previous message's Message-ID, creating a parent-child chain.
- **References**: A space-separated list of all ancestor Message-IDs in the conversation (capped at 20 to prevent header bloat).

When Gmail sees a new email whose `In-Reply-To` and `References` match an existing thread, it groups them together automatically.

## Nested Reply Context

When a new message arrives, PenguinConnect looks up the previous message in the conversation and constructs a quoted reply block — just like what you see in normal email threads:

**Plain text:**
```
hello two

On Mar 4, 2026 at 9:00 AM, Ethan wrote:
hello one
```

**HTML:**
```html
<p>hello two</p>
<div class="gmail_quote">
  <div class="gmail_attr">On Mar 4, 2026 at 9:00 AM, Ethan wrote:</div>
  <blockquote>hello one</blockquote>
</div>
```

Gmail recognizes the `gmail_quote` class and renders it with proper quote styling and collapse behavior.

## Gmail Import API

Messages are injected using Gmail's `messages.import()` API (not `send()`). This is important because:

- `internalDateSource: "dateHeader"` preserves the original message timestamp, so your thread shows messages in the right chronological order
- `neverMarkSpam: true` prevents bridge messages from hitting spam
- The UNREAD label is set atomically based on the message's read state in Apple Messages

The first import creates a Gmail thread ID, which is persisted locally. All subsequent messages reference this thread.

## Reply Routing (Gmail to Apple Messages)

When you reply from Gmail, PenguinConnect:

1. Detects the reply by polling for new messages sent to the conversation's alias email
2. Strips all quoted content — it uses HTML-aware parsing to remove `gmail_quote` blocks, signature markers, and forwarded content, extracting only the net-new text you typed
3. Validates the sender (must be your primary Gmail or a verified send-as alias)
4. Resolves the exact Apple Messages route for the conversation
5. Sends via AppleScript to the correct chat

If the quoted text can't be cleanly separated from new text, the reply is rejected visibly in Gmail rather than sending garbage to your contact.

## DM Unification

If you text the same person over iMessage AND SMS, those are separate conversations with separate `conversation_id`s (because the provider is part of the hash). Each gets its own Gmail thread. Group chats are always separate per exact chat membership.
