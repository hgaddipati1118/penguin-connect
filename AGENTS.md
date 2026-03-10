# Repo Skills

## Available skills

- `open-source-standards`: Open source contribution standards for PenguinConnect, including commit and PR hygiene plus repo-specific iMessage and Gmail privacy/runtime expectations. Use when making or reviewing code changes, planning fixes, writing commit messages, preparing pull requests or issue updates, or checking whether a change follows maintainable open source practices. File: `skills/open-source-standards/SKILL.md`
- `messaging-channel-integration`: Blueprint for extending PenguinConnect beyond iMessage by reusing the current bridge architecture for new providers such as WhatsApp or Telegram. Use when planning provider abstractions, schema changes, sync flows, auth/setup, or tests for new messaging channels. File: `skills/messaging-channel-integration/SKILL.md`

## Usage

- Load `skills/open-source-standards/SKILL.md` before substantial code changes, commits, PR text, issue follow-up, or release-facing fixes.
- Load `skills/messaging-channel-integration/SKILL.md` before refactoring the bridge for multi-provider support or adding a new source channel.
- Prefer repo-specific docs and behavior contracts over generic guidance when they conflict.
