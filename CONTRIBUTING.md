# Contributing to PenguinConnect

PenguinConnect handles private Gmail and Apple Messages data on a local macOS
machine. Contributions should stay focused, reviewable, and explicit about what
was actually verified.

## Quick Links

- [`README.md`](./README.md): project overview and install flow
- [`docs/PENGUIN_CONNECT.md`](./docs/PENGUIN_CONNECT.md): setup, troubleshooting,
  and operations
- [`AGENTS.md`](./AGENTS.md): coding-agent quick start and repo constraints
- [`SECURITY.md`](./SECURITY.md): security-sensitive reporting guidance

## Project Invariants

- Keep the bridge local-only and macOS-only unless the change explicitly
  expands that contract.
- Treat `conversation_id` as the primary identity for a conversation.
- Apple Messages routing must fail closed. If route resolution is ambiguous
  across `iMessage`, `SMS`, or `RCS`, do not send.
- Apple Messages direct messages may unify into one logical thread across
  sibling routes. Group chats stay separate per exact chat.
- Gmail-to-Apple-Messages delivery should send only net-new text. Do not
  reintroduce quoted reply chains or forwarded history when parsing is
  uncertain.
- Do not add logging for OAuth credentials, raw message bodies, phone numbers,
  email addresses, or contact exports unless the task explicitly requires it and
  the risk is documented.
- Do not place real names, phone numbers, email addresses, alias emails, raw
  message content, or OAuth material into commit messages, PR descriptions,
  issue comments, screenshots, docs examples, or pasted logs. Use redacted or
  synthetic examples instead.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Create the virtual environment under [`server/`](./server) and install
   [`server/requirements.txt`](./server/requirements.txt).
3. Run bridge and setup commands from `Terminal.app` on macOS with Full Disk
   Access enabled.

```bash
cd server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cd ..
```

Common commands:

```bash
./scripts/penguin_connect_setup.py --gmail you@gmail.com
./scripts/run_penguin_connect_bridge.sh
./scripts/check.sh
```

## Making Changes

- Keep each PR to one logical change when practical. Split cleanup, behavior
  changes, and refactors instead of mixing them.
- Read the relevant code and docs before editing. For new messaging providers,
  also review `skills/messaging-channel-integration/SKILL.md`.
- Update tests when behavior changes or when fixing a regression.
- Update README or docs when setup steps, operational commands, environment
  variables, or behavior contracts change.
- Avoid drive-by formatting churn that hides the real diff.
- Remove placeholders, stale TODOs, speculative comments, and dead code before
  sending a change out for review.

## Verification

Run the narrowest useful checks first, then the broader repo check when it is
relevant:

```bash
cd server
venv/bin/python -m unittest -v test_penguin_connect
cd ..
./scripts/check.sh
```

In your PR or handoff, state exactly what you ran and what you did not run. Do
not claim manual Apple Messages, Gmail OAuth, or Full Disk Access verification
unless you performed it on a suitable macOS setup.

## Pull Requests

Before opening a PR:

- rebase or merge the latest `main` as needed without discarding unrelated work
- keep the diff focused and explain any large or multi-part change
- fill out the PR template completely
- link the relevant issue when one exists
- include scrubbed screenshots or logs only when they add value and do not
  expose private data
- scrub the PR title, body, and attached artifacts so they do not reveal live
  names, email addresses, alias addresses, phone numbers, or message text

PR summaries should cover:

- the problem
- why it matters
- what changed
- what did not change
- how the change was verified
- remaining operator, migration, or privacy risk

## Commit Style

- Use Conventional Commits unless the maintainers ask for a different format.
- Keep the subject line imperative and specific, ideally within 72 characters.
- Use `Fixes #123` only when the merged change will actually close the issue.
- If the worktree is dirty, commit only the files that belong to your completed
  change.
- Review the staged diff and commit subject/body for accidental PII before
  pushing.

## Reporting Bugs And Ideas

- Use the issue templates under `.github/ISSUE_TEMPLATE/`.
- For setup questions, check [`docs/PENGUIN_CONNECT.md`](./docs/PENGUIN_CONNECT.md)
  first so issues stay focused on actionable defects or feature requests.
- For security-sensitive problems, follow [`SECURITY.md`](./SECURITY.md) instead
  of opening a detailed public issue.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](./LICENSE).
