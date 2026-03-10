# Open Source Standards Sources

Use this reference when you need the rationale behind the skill or want the source links for a recommendation.

## Source-backed defaults

### Keep changes reviewable

- Git's `SubmittingPatches` guidance says each patch should make a single logical change and stay reviewable as part of a patch series.
- Apply that here by separating bug fixes, refactors, formatting churn, and docs-only updates when practical.

### Write strong commit messages

- GitHub Docs says commit messages should start with a short, imperative summary and may include a more detailed body.
- Git's `SubmittingPatches` recommends a one-line summary of about 72 characters, followed by paragraphs that explain why the change is needed and how it works.
- Apply that here even when using Conventional Commits. The `type(scope): subject` line should still stay concise and imperative.

### Use structured commit types when no repo format exists

- Conventional Commits 1.0.0 defines the `type(scope): description` pattern and the `BREAKING CHANGE:` footer.
- This repo has no established commit taxonomy yet, so use Conventional Commits as the default format unless the user or repo instructions say otherwise.

### Link work to issues

- GitHub Docs supports linking and automatically closing issues from pull requests with keywords such as `Fixes #123`.
- Use closing keywords only when the merged change truly resolves the issue.

### Treat AI output as untrusted draft material

- Git's `SubmittingPatches` guidance allows AI assistance only when the contributor reviews, understands, and takes responsibility for every part of the result.
- Apply that here by verifying code paths, tests, docs, and user-facing claims before reporting completion.

## PenguinConnect interpretation

- Because PenguinConnect handles private iMessage and Gmail content, open source standards here also include privacy-safe defaults, minimal logging, honest verification, and explicit communication about macOS-only runtime assumptions.
- A change is not "done" if it silently weakens local-only guarantees, sender-gate behavior, alias ownership rules, or documented setup/operations without surfacing that impact.

## Sources

- GitHub Docs, About commits: <https://docs.github.com/en/get-started/using-git/about-commits>
- Git documentation, SubmittingPatches: <https://git-scm.com/docs/SubmittingPatches>
- Conventional Commits 1.0.0: <https://www.conventionalcommits.org/en/v1.0.0/>
- GitHub Docs, Linking a pull request to an issue: <https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue>
