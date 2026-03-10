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

### Keep community-health files explicit

- GitHub's community profile checklist looks for recommended community health
  files such as `README`, `CODE_OF_CONDUCT`, `LICENSE`, `CONTRIBUTING`, and
  `SECURITY`, plus valid issue templates.
- Apply that here by keeping PenguinConnect's repo-health files intentional
  rather than implicit or scattered.

### Use contribution and issue templates to reduce review churn

- GitHub Docs says a `CONTRIBUTING.md` file helps contributors submit well-formed
  pull requests and useful issues, and GitHub surfaces that file in multiple
  repository entry points.
- GitHub Docs says issue and pull request templates standardize the information
  maintainers want contributors to include.
- Apply that here by keeping PR expectations, issue intake, and security
  reporting concrete and visible in-repo.

### Treat AI output as untrusted draft material

- Git's `SubmittingPatches` guidance allows AI assistance only when the contributor reviews, understands, and takes responsibility for every part of the result.
- Apply that here by verifying code paths, tests, docs, and user-facing claims before reporting completion.

## PenguinConnect interpretation

- Because PenguinConnect handles private Apple Messages and Gmail content, open source standards here also include privacy-safe defaults, minimal logging, honest verification, and explicit communication about macOS-only runtime assumptions.
- Apple Messages routing must fail closed: if a legacy identifier could map to multiple `iMessage` / `SMS` / `RCS` chats, the bridge should not send until the exact route is resolved.
- Gmail-to-Apple-Messages delivery should be net-new-text-first: strip quoted reply chains, forwarded headers, and boilerplate before sending, and prefer dropping ambiguous quoted content over echoing private thread history back into chat.
- Keep a short user-facing README and a separate agent-facing guide when the
  repo benefits from dedicated coding-agent setup and invariant instructions.
- Keep the MIT license and community-health files visible from the README so new
  contributors do not have to guess where contribution, security, or agent
  onboarding rules live.
- A change is not "done" if it silently weakens local-only guarantees, sender-gate behavior, alias ownership rules, or documented setup/operations without surfacing that impact.

## Sources

- GitHub Docs, About commits: <https://docs.github.com/en/get-started/using-git/about-commits>
- Git documentation, SubmittingPatches: <https://git-scm.com/docs/SubmittingPatches>
- Conventional Commits 1.0.0: <https://www.conventionalcommits.org/en/v1.0.0/>
- GitHub Docs, Linking a pull request to an issue: <https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue>
- GitHub Docs, About community profiles for public repositories: <https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories>
- GitHub Docs, Setting guidelines for repository contributors: <https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors>
- GitHub Docs, Configuring issue templates for your repository: <https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository>
- GitHub Docs, Adding a security policy to your repository: <https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/adding-a-security-policy-to-your-repository>
- GitHub Docs, Adding a code of conduct to your project: <https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-code-of-conduct-to-your-project>
- GitHub Docs, Adding a license to a repository: <https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-license-to-a-repository>
