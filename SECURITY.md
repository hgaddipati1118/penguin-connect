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
an HTTPS tunnel and require both the bearer and rotating-code secrets stored in macOS Keychain. Do not expose the
Penguin FastAPI port, Apple Messages data, SQLite files, or the WhatsApp bridge API directly to
the Internet.

The remote MCP endpoint must expose only the scopes and providers in its validated built-in
policy. An invalid or missing policy fails closed to the legacy WhatsApp-only profile. The
expanded `read-only` and `slashy` profiles may expose iMessage, WhatsApp, and Contacts tools,
but must not expose files, attachment paths, attachment sends, search-index administration,
Slack, Telegram, Gmail, or the underlying Penguin and WhatsApp APIs. Provider filtering must
use the source provider before normalization so an unknown channel cannot be treated as
iMessage.

Every remote MCP request must authenticate with the long install bearer plus the six-character
code derived for the Mac's current local day. The code alone is insufficient, previous-day
credentials fail, and online code guesses must be rate limited. Every remote write must also
require a short-lived one-use confirmation bound to the exact request. That confirmation
protects payload integrity but is not a second human approval: entering today's code grants the
configured profile for that day. Existing conversation sends must resolve an exact allowed
conversation. A new one-person iMessage destination must be staged for review rather than sent
through a guessed route. An iMessage group is staged by default; actual creation is permitted
only through an explicitly configured loopback BlueBubbles Private API backend, with exact
participants, a non-empty first message, and the backend identity included in the one-use
confirmation. If that backend is unavailable at confirmation time, fail closed rather than
falling back to a draft. WhatsApp group creation must accept only exact unique phone numbers or
user JIDs and must remain behind the loopback-only bridge.

The optional BlueBubbles origin must resolve directly to loopback and must never contain URL
credentials, query parameters, or fragments. Store its password in macOS Keychain and pass it to
helpers over stdin, never argv or environment variables. Never expose BlueBubbles through
Penguin's tunnel. BlueBubbles Private API requires disabling SIP and injecting into Messages;
Penguin must disclose that reduced Mac protection and must never disable those protections or
install BlueBubbles automatically.

Packaged contact writes must use Penguin's native Contacts helper. Authorize that exact helper
once during local setup, send contact payloads over stdin, and never put names, phone numbers, or
email addresses in process arguments. Remote contact writes still require the daily code and an
exact one-use confirmation; the macOS permission is not a per-action approval gate.

Treat the remote MCP bearer and daily-code derivation secret as private message and contact read
credentials. Never put either in `.env`, launchd plists, commits, URLs, screenshots, or logs.
The six-character display code may be shown locally, but never log a complete connection bundle.
Rotate the bearer immediately if it
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
