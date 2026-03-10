---
name: open-source-standards
description: Open source contribution standards for PenguinConnect, including commit and pull request hygiene plus repo-specific Apple Messages and Gmail privacy/runtime expectations. Use when Codex is making or reviewing code changes, planning fixes, writing commit messages, preparing pull requests or issue updates, or checking whether a change follows maintainable open source practices. Especially useful before commits, refactors, bug fixes, docs updates, and release-facing changes in this repo.
---

# Open Source Standards

Use this skill to keep PenguinConnect changes small, reviewable, privacy-safe, and honest about what was verified.

Read `references/standards.md` when you need the source-backed rationale or exact conventions behind these defaults.

## Workflow

1. Inspect local instructions first.
- Follow `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and
  existing code conventions before generic norms.
- Treat `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/`, and
  `.github/copilot-instructions.md` as part of the repo contract when they are
  present.

2. Scope the change tightly.
- Make one logical change at a time.
- Split mixed behavior changes, refactors, and cleanup into separate commits when practical.
- Preserve documented product invariants unless the user explicitly asks to change them.

3. Protect PenguinConnect invariants.
- Keep the bridge macOS-only and local-only unless the task explicitly changes that contract.
- Treat `conversation_id` as the primary identity.
- Treat Apple Messages routes as exact `chat.guid` plus provider context, not just `chat_identifier`.
- Preserve the Apple Messages product rule: direct messages may unify into one logical thread, but group chats stay separate.
- Preserve the Apple Messages DM sync rule: unified direct-message threads must read across sibling `iMessage` / `RCS` / `SMS` routes when the product treats them as one chat.
- Preserve one active alias per conversation.
- Do not weaken sender-gate protections.
- Do not send when route resolution is ambiguous or unresolved.
- Gmail-to-Apple-Messages delivery should prefer net-new text only; do not reintroduce synthetic quoted context or forward raw reply chains when the parser is uncertain.
- Do not add logging for OAuth credentials, raw message bodies, or contact data unless the task explicitly requires it and the user accepts the risk.

4. Verify before claiming success.
- Run the narrowest relevant checks first.
- Use `./scripts/check.sh` for broad repo validation when feasible.
- Run targeted `unittest` modules under `server/` when faster, then say exactly what you did and did not run.
- Never claim manual Apple Messages send, Gmail OAuth, Full Disk Access, or Terminal.app verification unless you actually performed it on a suitable macOS setup.

5. Update surrounding artifacts.
- Update docs when setup steps, operational commands, environment variables, or bridge behavior change.
- Update community-health files when contributor workflow, PR expectations,
  security reporting, licensing, or agent onboarding changes.
- Add or adjust tests when behavior changes or when fixing a regression.
- Call out migrations, manual follow-ups, or operator risk in the final summary.

## Community Health Defaults

- Keep core open source repo-health files explicit and easy to find:
  `LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, issue templates, and a PR template.
- Prefer a short user-facing `README.md` plus a separate agent-facing guide when
  coding agents need compressed setup, verification, and invariant context.
- Link the README to contributor, security, license, and agent docs so both
  humans and agents can discover them quickly.
- Keep issue templates focused on actionable bug reports and feature requests;
  redirect security-sensitive reports to `SECURITY.md`.
- Keep PR templates explicit about problem, scope, verification, and remaining
  risk instead of relying on reviewer guesswork.

## Commit Standards

- Prefer Conventional Commits when the repo does not already enforce another format.
- Use one of: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`.
- Keep the subject line imperative, specific, and within about 72 characters.
- Add an optional scope when it adds clarity, for example `fix(sync): avoid duplicate Gmail import`.
- Explain why the change exists and any notable behavior or risk in the commit body.
- Add a `BREAKING CHANGE:` footer when a change intentionally alters contracts or behavior.

## Issue And PR Standards

- Link the change to the relevant issue when one exists.
- Use GitHub closing keywords such as `Fixes #123` when the change should close the issue on merge.
- Write reviewable summaries: problem, approach, verification, remaining risk.
- Fill in the repository PR template instead of bypassing it.
- Route public bug and feature intake through the repository issue templates when
  they exist.
- Treat `SECURITY.md` as the reporting path for privacy-sensitive or
  vulnerability-style issues.
- Explain why a diff is large if it cannot be made smaller.
- Raise unanswered questions instead of hiding uncertainty.

## AI Guardrails

- Treat model output as draft material that must be reviewed, understood, and tested.
- Remove placeholders, speculative comments, dead code, and stale TODOs before finishing.
- Do not invent benchmarks, screenshots, repro steps, or passing test results.
- Do not attribute authorship to an AI tool in commits or code unless the user explicitly asks for that.

## Response Pattern

Report:

- what changed
- why it changed
- what you verified
- what you could not verify
- any follow-up risk or operator action
