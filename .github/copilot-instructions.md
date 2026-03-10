# PenguinConnect GitHub Agent Instructions

Read [`AGENTS.md`](../AGENTS.md), [`README.md`](../README.md), and
[`CONTRIBUTING.md`](../CONTRIBUTING.md) before making substantial changes.

## Quick Commands

```bash
cp .env.example .env
cd server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cd ..
./scripts/run_penguin_connect_bridge.sh
./scripts/check.sh
```

## Non-Negotiable Invariants

- Keep the bridge local-only and macOS-only unless the task explicitly changes
  that contract.
- Treat `conversation_id` as the primary identity.
- Apple Messages route resolution must fail closed when ambiguous.
- Group chats stay separate; direct messages may unify across sibling routes.
- Gmail-to-chat sends only net-new text.
- Do not log or commit OAuth secrets, raw message bodies, or contact exports.

## Verification

- Prefer targeted `unittest` runs first, then `./scripts/check.sh`.
- Never claim manual Gmail OAuth, Apple Messages access, or Full Disk Access
  verification unless you actually performed it.
