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
- anything that breaks the local-only runtime assumption
- unsafe handling of contact exports, aliases, or local SQLite data

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
