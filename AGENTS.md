# Repo Skills

## Available skills

- `open-source-standards`: Open source contribution standards for PenguinConnect, including commit and PR hygiene plus repo-specific Apple Messages and Gmail privacy/runtime expectations. Use when making or reviewing code changes, planning fixes, writing commit messages, preparing pull requests or issue updates, or checking whether a change follows maintainable open source practices. File: `skills/open-source-standards/SKILL.md`
- `messaging-channel-integration`: Blueprint for extending PenguinConnect beyond the current Apple Messages adapter by reusing the bridge architecture for new providers such as WhatsApp or Telegram. Use when planning provider abstractions, schema changes, sync flows, auth/setup, or tests for new messaging channels. File: `skills/messaging-channel-integration/SKILL.md`

## Usage

- Load `skills/open-source-standards/SKILL.md` before substantial code changes, commits, PR text, issue follow-up, or release-facing fixes.
- Load `skills/messaging-channel-integration/SKILL.md` before refactoring the bridge for multi-provider support or adding a new source channel.
- Prefer repo-specific docs and behavior contracts over generic guidance when they conflict.

## Commit And Push Policy

- After completing any feature, fix, refactor, script update, test update, or documentation change, create a git commit and push it to `origin` on the working branch.
- Do not leave completed work only in the local working tree unless the user explicitly asks not to commit/push yet.
- If there are unrelated local changes, commit only the files relevant to the requested task.
