# Security Policy

PenguinConnect processes private Gmail and Apple Messages content on a local
macOS machine. Bugs that weaken local-only guarantees, sender-gate protections,
route ambiguity checks, secret storage, or message privacy should be treated as
security-sensitive.

## Security Scope

Treat the following as security-relevant:

- OAuth credential exposure or unsafe secret storage
- raw Gmail or Apple Messages content leaking through logs, screenshots, or test
  fixtures
- sender-gate bypasses
- ambiguous route resolution that could send to the wrong Apple Messages thread
- anything that exposes an origin service beyond the loopback-only runtime boundary
- remote MCP endpoints that bypass bearer authentication, TLS ingress, or loopback-only origin binding
- WhatsApp pairing codes or session material exposed through status responses, logs, or non-loopback listeners
- unsafe handling of contact exports, aliases, or local SQLite data

## Remote MCP Boundary

Penguin's optional remote MCP process must bind only to a loopback address. Publish it through
an HTTPS tunnel and require the bearer token stored in macOS Keychain. Do not expose the
Penguin FastAPI port, Apple Messages data, SQLite files, or the WhatsApp bridge API directly to
the Internet.

The remote MCP endpoint must expose only the scopes and providers in its validated built-in
policy. An invalid or missing policy fails closed to the legacy WhatsApp-only profile. The
expanded `read-only` and `slashy` profiles may expose iMessage, WhatsApp, and Contacts tools,
but must not expose files, attachment paths, attachment sends, search-index administration,
Slack, Telegram, Gmail, or the underlying Penguin and WhatsApp APIs. Provider filtering must
use the source provider before normalization so an unknown channel cannot be treated as
iMessage.

Every remote write must require a short-lived one-use confirmation bound to the exact request
plus a local approval click on the Mac. A second MCP call by itself is not human approval and
does not protect against prompt injection. Existing conversation sends must resolve an exact
allowed conversation. A new iMessage destination must be staged for review rather than sent
through a guessed route.

Treat the remote MCP bearer token as a private message and contact read credential. Never put
it in `.env`, launchd plists, commits, URLs, screenshots, or logs. Rotate it immediately if it
may have been copied into an untrusted system. Keep Quick Tunnels temporary. The consumer setup
defaults to a stable Tailscale Funnel on dedicated HTTPS port `10000`; the bearer is still
required because Funnel intentionally accepts public Internet traffic.

The WhatsApp pairing status endpoint may report only coarse state and whether a QR is available.
It must never return the raw pairing code. The QR image endpoint must bind to `127.0.0.1`, send
`Cache-Control: no-store`, and pairing codes must not be printed to logs by default.

## Reporting A Vulnerability

- Prefer GitHub private vulnerability reporting if it is enabled for the
  repository:
  `https://github.com/hgaddipati1118/penguin-connect/security`
- Do not publish OAuth JSON files, access tokens, refresh tokens, `chat.db`
  copies, raw message bodies, contact exports, or screenshots containing private
  data.
- If private reporting is not available, open a minimal public issue that asks
  for a private contact path and omit exploit details and sensitive artifacts.

## What To Include

- a short description of the issue and the user impact
- the commit hash or branch where you reproduced it
- macOS version and whether Terminal Full Disk Access was involved
- minimal reproduction steps that use scrubbed or synthetic data
- the affected area, such as Gmail OAuth, sync, alias routing, Apple Messages
  reads, or local storage
- any immediate mitigation or containment advice you already validated

## Safe Artifact Handling

Do not attach or paste:

- Google OAuth client JSON files
- Gmail access or refresh tokens
- copies of `~/Library/Messages/chat.db`
- raw email bodies or Apple Messages transcripts
- screenshots with private names, addresses, phone numbers, or message content

When logs are needed, redact identifiers and share the smallest snippet that
still proves the issue.

## Immediate Containment

If you suspect secret or message exposure:

- disconnect the affected Gmail account from the local bridge
- rotate the Google OAuth client secret or token if it may have been exposed
- avoid posting logs or screenshots until sensitive fields are removed
- stop any local bridge process that may still be sending or syncing affected
  data
