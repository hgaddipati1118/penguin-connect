# PenguinConnect Agent Guide

This repository is a local-only macOS bridge between Gmail and messaging
providers. Use this file as the fast path for coding agents before touching
code, docs, or automation.

## Quick Start

1. Copy `.env.example` to `.env`.
2. Install backend dependencies:

   ```bash
   cd server
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   cd ..
   ```

3. Run guided setup:

   ```bash
   ./scripts/penguin_connect_setup.py --gmail you@gmail.com
   ```

4. Start the bridge:

   ```bash
   ./scripts/run_penguin_connect_bridge.sh
   ```

5. Run verification:

   ```bash
   ./scripts/check.sh
   ```

Targeted test loop:

```bash
cd server
venv/bin/python -m unittest -v test_penguin_connect
```

Important runtime constraint: setup and bridge commands must be run from
`Terminal.app` with Full Disk Access enabled, otherwise Apple Messages
`chat.db` reads will fail.

## Repo Map

- `README.md`: user-facing project overview and install instructions
- `docs/PENGUIN_CONNECT.md`: setup, troubleshooting, operational commands
- `server/`: FastAPI app, sync logic, local DB, tests
- `server/channels/`: provider adapters; Apple Messages is implemented today
- `scripts/`: setup, doctor, sync, audit, and launch helpers
- `skills/`: repo-local instructions for open source workflow and future channel
  integrations
- `.github/`: issue templates, PR template, and GitHub agent instructions

## Product And Safety Invariants

- Keep the bridge local-only on `127.0.0.1` unless the task explicitly changes
  that contract.
- The runtime is macOS-only today because Apple Messages is the first source
  adapter.
- `conversation_id` is the primary logical identity.
- Apple Messages direct messages may unify across sibling `iMessage`, `SMS`,
  and `RCS` routes; group chats stay separate per exact chat.
- Route resolution must fail closed. If the exact Apple Messages route is
  ambiguous, do not send.
- Keep one active alias email per conversation.
- Gmail-to-chat delivery should send net-new text only. Do not reintroduce
  quoted reply chains or synthetic context when parsing is uncertain.
- Do not add logging for OAuth credentials, raw message bodies, contact exports,
  or other private message content unless the task explicitly requires it and
  the risk is documented.

## Working Rules

- Read `README.md`, `CONTRIBUTING.md`, and `SECURITY.md` before broad changes.
- Keep diffs focused. Avoid mixing behavior changes, refactors, and cleanup
  without a strong reason.
- Update docs when setup steps, environment variables, commands, or behavior
  contracts change.
- Add or adjust tests when behavior changes or when fixing a regression.
- Prefer targeted `unittest` runs first, then `./scripts/check.sh` when the
  broader suite is relevant.
- Never claim Gmail OAuth, Apple Messages access, Terminal Full Disk Access, or
  manual send verification unless you actually performed it on a suitable macOS
  machine.

## Commit And PR Expectations

- Use Conventional Commits when practical: `feat`, `fix`, `docs`, `refactor`,
  `test`, `chore`, `perf`, `build`, `ci`.
- Keep commit subjects imperative and specific.
- If the worktree is dirty, commit only the files relevant to the task you
  completed.
- PRs should explain:
  - the problem
  - the approach
  - what was verified
  - what was not verified
  - any privacy, migration, or operator risk

## Agent Entry Points

- `AGENTS.md`: canonical repo guidance for coding agents
- `CLAUDE.md`: lightweight pointer for tools that look for a Claude-specific
  file
- `.github/copilot-instructions.md`: GitHub Copilot and coding-agent quick
  instructions

## Repo Skills

### Available Skills

- `open-source-standards`: Open source contribution standards for
  PenguinConnect, including commit and PR hygiene plus repo-specific Apple
  Messages and Gmail privacy/runtime expectations. Use when making or reviewing
  code changes, planning fixes, writing commit messages, preparing pull requests
  or issue updates, or checking whether a change follows maintainable open
  source practices. File: `skills/open-source-standards/SKILL.md`
- `messaging-channel-integration`: Blueprint for extending PenguinConnect
  beyond the current Apple Messages adapter by reusing the bridge architecture
  for new providers such as WhatsApp or Telegram. Use when planning provider
  abstractions, schema changes, sync flows, auth/setup, or tests for new
  messaging channels. File: `skills/messaging-channel-integration/SKILL.md`

### Usage

- Load `skills/open-source-standards/SKILL.md` before substantial code changes,
  commits, PR text, issue follow-up, or release-facing fixes.
- Load `skills/messaging-channel-integration/SKILL.md` before refactoring the
  bridge for multi-provider support or adding a new source channel.
- Prefer repo-specific docs and behavior contracts over generic guidance when
  they conflict.

### Commit And Push Policy

- After completing any feature, fix, refactor, script update, test update, or
  documentation change, create a git commit and push it to `origin` on the
  working branch.
- Do not leave completed work only in the local working tree unless the user
  explicitly asks not to commit or push yet.
- If there are unrelated local changes, commit only the files relevant to the
  requested task.
